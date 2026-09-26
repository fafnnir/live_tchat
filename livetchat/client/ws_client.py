# livetchat/client/ws_client.py
# Connexion WebSocket dans un thread dédié, avec reconnexion automatique.
import json
import ssl
import threading

import websocket
from PyQt6.QtCore import QObject, pyqtSignal

from livetchat.client.config import CLIENT_ID, TLS_VERIFY, ws_url
from livetchat.shared.version import VERSION


class WsWorker(QObject):
    status_changed = pyqtSignal(str)
    presence = pyqtSignal(list)
    display_start = pyqtSignal(dict)
    display_end = pyqtSignal(dict)
    joined = pyqtSignal(str)
    auth_error = pyqtSignal()

    def __init__(self, token: str, username: str, channel: str):
        super().__init__()
        self.token, self.username, self.channel = token, username, channel
        self._ws: websocket.WebSocketApp | None = None
        self._alive = True
        self._paused = threading.Event()     # posé quand le jeton est refusé
        self._wake = threading.Event()

    # ── Boucle du thread ─────────────────────────────────────
    def run(self):
        while self._alive:
            if self._paused.is_set():
                self._wake.wait(1)
                self._wake.clear()
                continue
            try:
                self._ws = websocket.WebSocketApp(
                    ws_url(),
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=lambda _ws, e: print(f"[WS] erreur : {e}"),
                    on_close=lambda *_: None,
                )
                sslopt = {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": TLS_VERIFY} if isinstance(TLS_VERIFY, str) else None
                self._ws.run_forever(ping_interval=25, ping_timeout=10, sslopt=sslopt)
            except Exception as e:
                print(f"[WS] {e}")
            if self._alive and not self._paused.is_set():
                self.status_changed.emit("🔌  Déconnecté — reconnexion…")
                self._wake.wait(3)
                self._wake.clear()

    def _on_open(self, ws):
        ws.send(json.dumps({
            "type": "hello", "token": self.token, "username": self.username,
            "channel": self.channel, "client_id": CLIENT_ID, "version": VERSION,
        }))

    def _on_message(self, _ws, raw):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        mtype = msg.get("type")
        if mtype == "presence":
            self.presence.emit(msg.get("channels", []))
        elif mtype == "display_start":
            self.display_start.emit(msg)
        elif mtype == "display_end":
            self.display_end.emit(msg)
        elif mtype in ("welcome", "joined"):
            self.channel = msg.get("channel", self.channel)
            self.joined.emit(self.channel)
            self.status_changed.emit(f"✅  Connecté → #{self.channel}")
        elif mtype == "auth_error":
            self._paused.set()
            self.auth_error.emit()

    # ── Appelé depuis l'UI ───────────────────────────────────
    def _send(self, payload: dict):
        try:
            if self._ws and self._ws.sock and self._ws.sock.connected:
                self._ws.send(json.dumps(payload))
        except Exception as e:
            print(f"[WS] envoi impossible : {e}")

    def join(self, channel: str):
        self.channel = channel
        self._send({"type": "join", "channel": channel})

    def rename(self, username: str):
        self.username = username
        self._send({"type": "rename", "username": username})

    def skip(self, media_id: str):
        self._send({"type": "skip", "media_id": media_id})

    def resume(self, token: str):
        self.token = token
        self._paused.clear()
        self._wake.set()

    def stop(self):
        self._alive = False
        self._wake.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
