# livetchat/client/api.py
# Appels HTTP vers le serveur (toujours depuis un thread, jamais dans le thread de l'UI).
import requests

from livetchat.client.config import API_BASE, CLIENT_ID, TLS_VERIFY

ERRORS = {
    "BAD_PASSWORD": "Mot de passe incorrect.",
    "TOO_MANY_ATTEMPTS": "Trop d'essais ratés, réessaie dans 15 minutes.",
    "AUTH_REQUIRED": "Session expirée, reconnecte-toi.",
    "UNSUPPORTED_FORMAT": "Format non supporté (ou fichier corrompu).",
    "QUEUE_FULL": "La file de ce salon est pleine, réessaie plus tard.",
    "TOO_MANY_UPLOADS": "Trop d'envois, attends une minute.",
    "UNKNOWN_CHANNEL": "Salon inconnu.",
    "EXPIRED": "Le média n'est plus disponible.",
    "NO_RELEASE": "Aucune version publiée sur le serveur.",
    "BAD_MANIFEST": "Manifest de mise à jour invalide.",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        if code.startswith("TOO_LARGE_MAX_"):
            text = f"Fichier trop lourd (max {code.rsplit('_', 1)[-1].replace('MB', '')} Mo)."
        else:
            text = ERRORS.get(code, f"Erreur serveur ({status}).")
        super().__init__(text)


def _raise_for(r: requests.Response):
    if r.ok:
        return
    code = ""
    try:
        code = str(r.json().get("detail", ""))
    except Exception:
        pass
    raise ApiError(r.status_code, code)


def login(password: str) -> str:
    r = requests.post(f"{API_BASE}/api/login", json={"password": password}, timeout=15, verify=TLS_VERIFY)
    _raise_for(r)
    return r.json()["token"]


def upload(token: str, data: bytes, filename: str, *, display_time: float, display_text: str,
           username: str, channel: str) -> dict:
    r = requests.post(
        f"{API_BASE}/api/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, data)},
        data={
            "display_time": str(display_time),
            "display_text": display_text,
            "username": username,
            "channel": channel,
            "client_id": CLIENT_ID,
        },
        timeout=120,
        verify=TLS_VERIFY,
    )
    _raise_for(r)
    return r.json()


def fetch_media(token: str, url_path: str, max_bytes: int = 30 * 1024 * 1024) -> bytes:
    """Télécharge un média EN MÉMOIRE uniquement (rien n'est écrit sur le disque)."""
    if not url_path.startswith("/media/"):
        raise ApiError(400, "BAD_URL")
    buf = bytearray()
    with requests.get(f"{API_BASE}{url_path}", headers={"Authorization": f"Bearer {token}"},
                      stream=True, timeout=30, verify=TLS_VERIFY) as r:
        _raise_for(r)
        for chunk in r.iter_content(256 * 1024):
            buf += chunk
            if len(buf) > max_bytes:
                raise ApiError(413, "TOO_LARGE_MAX_30MB")
    return bytes(buf)


def fetch_manifest() -> dict:
    r = requests.get(f"{API_BASE}/manifest.json", timeout=10, verify=TLS_VERIFY)
    _raise_for(r)
    return r.json()
