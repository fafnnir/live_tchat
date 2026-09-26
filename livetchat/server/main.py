# livetchat/server/main.py
import asyncio
import json
import math
import os
import re
import secrets
from contextlib import asynccontextmanager

from fastapi import (
    Depends, FastAPI, File, Form, HTTPException, Request, UploadFile,
    WebSocket, WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from livetchat.server import settings
from livetchat.server.channel_queue import MEDIA, ChannelQueue, MediaItem
from livetchat.server.routes_manifest import router as manifest_router
from livetchat.server.security import (
    LoginGuard, RateLimiter, check_media_signature, check_session_token,
    clean_text, clean_username, make_session_token, secret_problem, verify_password,
)
from livetchat.server.validators import detect
from livetchat.server.ws_manager import Client, ConnectionManager
from livetchat.shared.version import VERSION

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
HELLO_TIMEOUT_S = 10
COPY_CHUNK = 256 * 1024

# ------------------------------------------------------------
# État global (un seul worker uvicorn)
# ------------------------------------------------------------
manager = ConnectionManager()
channel_queues: dict[str, ChannelQueue] = {}
login_guard = LoginGuard(settings.LOGIN_MAX_FAILS, settings.LOGIN_LOCK_S)
upload_limiter = RateLimiter(settings.UPLOADS_PER_MINUTE, 60)


PRESENCE_DEBOUNCE_S = 0.5
_presence_pending = False
_presence_tasks: set[asyncio.Task] = set()


async def broadcast_presence():
    """Regroupe les changements (arrivées, départs, pseudos, files) : au plus une diffusion
    de la présence à tout le monde toutes les 0,5 s, quel que soit le nombre d'événements."""
    global _presence_pending
    if _presence_pending:
        return
    _presence_pending = True

    async def later():
        global _presence_pending
        await asyncio.sleep(PRESENCE_DEBOUNCE_S)
        _presence_pending = False          # un changement pendant l'envoi relancera une diffusion
        await manager.broadcast_all(presence_snapshot())

    task = asyncio.create_task(later())
    _presence_tasks.add(task)              # garde une référence jusqu'à la fin
    task.add_done_callback(_presence_tasks.discard)


def presence_snapshot() -> dict:
    return {
        "type": "presence",
        "channels": [
            {
                "id": ch,
                "members": manager.members(ch),
                "queue": channel_queues[ch].queue_size(),
                "playing": channel_queues[ch].current is not None,
            }
            for ch in settings.ALLOWED_CHANNELS
        ],
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    os.makedirs(settings.MEDIA_DIR, exist_ok=True)
    # Les fichiers d'une exécution précédente ne sont plus référencés : on vide
    for fname in os.listdir(settings.MEDIA_DIR):
        try:
            os.remove(os.path.join(settings.MEDIA_DIR, fname))
        except Exception:
            pass
    for ch in settings.ALLOWED_CHANNELS:
        channel_queues[ch] = ChannelQueue(ch, manager, broadcast_presence)
    problem = secret_problem()
    if problem:
        raise RuntimeError(f"[SECURITY] {problem} — voir livetchat/deploy/env.example")
    if not settings.PASSWORD_HASH:
        print("[SECURITY] LTCHAT_PASSWORD_HASH absent : personne ne pourra se connecter")
    print(f"[START] LiveTchat serveur v{VERSION}")
    yield


app = FastAPI(title="LiveTchat", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(manifest_router)

if not settings.USE_X_ACCEL and os.path.isdir(settings.DOWNLOAD_DIR):
    # En prod c'est nginx qui sert /downloads
    from fastapi.staticfiles import StaticFiles
    app.mount("/downloads", StaticFiles(directory=settings.DOWNLOAD_DIR), name="downloads")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def require_auth(request: Request):
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else None
    if not check_session_token(token):
        raise HTTPException(status_code=401, detail="AUTH_REQUIRED")


# ------------------------------------------------------------
# Connexion (mot de passe partagé)
# ------------------------------------------------------------
class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    ip = client_ip(request)
    if login_guard.is_locked(ip):
        raise HTTPException(status_code=429, detail="TOO_MANY_ATTEMPTS")
    # scrypt est coûteux : on le sort de la boucle asyncio
    ok = await asyncio.to_thread(verify_password, body.password)
    if not ok:
        login_guard.fail(ip)
        print(f"[LOGIN] échec depuis {ip}")
        raise HTTPException(status_code=401, detail="BAD_PASSWORD")
    login_guard.success(ip)
    return {"token": make_session_token()}


@app.get("/api/channels", dependencies=[Depends(require_auth)])
def list_channels():
    return presence_snapshot()["channels"]


# ------------------------------------------------------------
# Upload
# ------------------------------------------------------------
@app.post("/api/upload", dependencies=[Depends(require_auth)])
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    display_time: float = Form(...),
    display_text: str = Form(""),
    username: str = Form("guest"),
    channel: str = Form("general"),
    client_id: str = Form(...),
):
    ip = client_ip(request)
    if channel not in settings.ALLOWED_CHANNELS:
        raise HTTPException(status_code=400, detail="UNKNOWN_CHANNEL")
    if not CLIENT_ID_RE.match(client_id):
        raise HTTPException(status_code=400, detail="BAD_CLIENT_ID")
    if not upload_limiter.allow(ip):
        raise HTTPException(status_code=429, detail="TOO_MANY_UPLOADS")
    queue = channel_queues[channel]
    if queue.is_full():
        raise HTTPException(status_code=429, detail="QUEUE_FULL")
    if queue.count_from(ip) >= settings.MAX_QUEUED_PER_IP:
        raise HTTPException(status_code=429, detail="QUEUE_USER_FULL")

    ext = os.path.splitext(file.filename or "")[1].lower()
    head = await file.read(4096)
    detected = detect(ext, head)
    if not detected:
        print(f"[UPLOAD] REJECT {ip} format='{ext}'")
        raise HTTPException(status_code=415, detail="UNSUPPORTED_FORMAT")
    kind, content_type = detected
    max_bytes = settings.MAX_BYTES[kind]

    # Nom aléatoire : aucune donnée utilisateur dans le chemin
    media_id = secrets.token_urlsafe(18)
    path = os.path.join(settings.MEDIA_DIR, media_id + ext)
    size = 0
    try:
        with open(path, "wb") as out:
            chunk = head
            while chunk:
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"TOO_LARGE_MAX_{max_bytes // (1024 * 1024)}MB")
                out.write(chunk)
                chunk = await file.read(COPY_CHUNK)
        os.chmod(path, 0o640)
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    finally:
        await file.close()

    if not math.isfinite(display_time):
        display_time = 5.0
    display_time = min(max(display_time, settings.MIN_DISPLAY_S), settings.MAX_DISPLAY_S[kind])
    uname = clean_username(username)

    item = MediaItem(
        media_id=media_id, kind=kind, path=path, content_type=content_type,
        display_time=display_time, display_text=clean_text(display_text, settings.MAX_TEXT_LEN),
        username=uname, sender_id=client_id, sender_ip=ip, channel=channel,
    )
    position = queue.push(item)
    print(f"[UPLOAD] {ip} '{uname}' #{channel} {kind} {size // 1024}KB {display_time:.0f}s pos={position}")
    await broadcast_presence()
    return {"media_id": media_id, "kind": kind, "queue_position": position}


# ------------------------------------------------------------
# Lecture d'un média : lien signé + temporaire, ET session valide
# ------------------------------------------------------------
@app.get("/media/{media_id}/{exp}/{sig}", dependencies=[Depends(require_auth)])
def get_media(media_id: str, exp: int, sig: str):
    item = MEDIA.get(media_id)
    if not item or not check_media_signature(media_id, exp, sig) or not os.path.isfile(item.path):
        raise HTTPException(status_code=404, detail="EXPIRED")
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if settings.USE_X_ACCEL:
        # nginx envoie le fichier lui-même (sendfile + Range), Python ne touche pas aux octets
        headers["X-Accel-Redirect"] = settings.X_ACCEL_PREFIX + os.path.basename(item.path)
        return Response(status_code=200, media_type=item.content_type, headers=headers)
    return FileResponse(item.path, media_type=item.content_type, headers=headers)


# ------------------------------------------------------------
# WebSocket : présence + diffusion
# ------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    if len(manager.clients) >= settings.MAX_WS_CLIENTS:
        await ws.close(code=1013)
        return

    # 1er message obligatoire : hello (avec le jeton), sinon on coupe
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S))
        assert isinstance(hello, dict) and hello.get("type") == "hello"
    except Exception:
        await ws.close(code=4000)
        return
    if not check_session_token(hello.get("token")):
        try:
            await ws.send_text(json.dumps({"type": "auth_error"}))
        finally:
            await ws.close(code=4001)
        return
    client_id = str(hello.get("client_id", ""))
    if not CLIENT_ID_RE.match(client_id):
        await ws.close(code=4002)
        return
    channel = hello.get("channel")
    if channel not in settings.ALLOWED_CHANNELS:
        channel = settings.ALLOWED_CHANNELS[0]

    client = Client(ws=ws, client_id=client_id, username=clean_username(hello.get("username")), channel=channel)
    manager.add(client)
    await manager.send(client, {"type": "welcome", "version": VERSION, "channel": channel})
    await broadcast_presence()

    try:
        while True:
            raw = await ws.receive_text()
            if not client.limiter.allow("msg"):
                continue
            try:
                msg = json.loads(raw)
                mtype = msg.get("type")
            except Exception:
                continue

            if mtype == "join" and msg.get("channel") in settings.ALLOWED_CHANNELS:
                client.channel = msg["channel"]
                await manager.send(client, {"type": "joined", "channel": client.channel})
                await broadcast_presence()
            elif mtype == "rename":
                new_name = clean_username(msg.get("username"))
                if new_name != client.username:
                    client.username = new_name
                    await broadcast_presence()
            elif mtype == "skip":
                queue = channel_queues[client.channel]
                if queue.skip(str(msg.get("media_id", "")), client.client_id):
                    print(f"[SKIP] '{client.username}' #{client.channel}")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS] erreur '{client.username}': {e}")
    finally:
        manager.remove(client)
        await broadcast_presence()


# ------------------------------------------------------------
# Entrée
# ------------------------------------------------------------
def main():
    import uvicorn
    uvicorn.run(
        "livetchat.server.main:app",
        host=settings.HOST,
        port=settings.PORT,
        workers=1,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ws_max_size=64 * 1024,
    )


if __name__ == "__main__":
    main()
