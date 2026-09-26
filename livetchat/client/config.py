# livetchat/client/config.py
import json
import os
import secrets
from pathlib import Path

_HERE = Path(__file__).parent


def _server_settings() -> dict:
    """client/server.json (non versionné) : {"api_base": "https://IP:8000"}, intégré dans l'exe."""
    try:
        return json.loads((_HERE / "server.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


API_BASE = (os.environ.get("LTCHAT_API_BASE") or _server_settings().get("api_base")
            or "https://127.0.0.1:8000").rstrip("/")

# Certificat du serveur, épinglé : le client ne fait confiance qu'à lui (pas aux autorités publiques)
SERVER_CERT = str(_HERE / "server_cert.pem")
TLS_VERIFY = SERVER_CERT if API_BASE.startswith("https://") else True


def ws_url() -> str:
    return API_BASE.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"


# Identifiant de cette instance (sert à savoir qui a envoyé quoi, pour "Passer pour tous")
CLIENT_ID = secrets.token_urlsafe(16)

CONFIG_PATH = Path.home() / ".tchat_config.json"
_DEFAULTS = {"username": "guest", "token": "", "on_top": True, "channel": "general",
             "screen": ""}   # screen : nom de l'écran d'affichage, "" = principal


def load_config() -> dict:
    cfg = dict(_DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, default in _DEFAULTS.items():
                    if isinstance(data.get(key), type(default)):
                        cfg[key] = data[key]
    except Exception:
        pass
    cfg["username"] = cfg["username"][:24] or "guest"
    cfg["channel"] = cfg["channel"][:40] or "general"   # le serveur corrige si le salon n'existe pas
    return cfg


def save_config(**updates) -> None:
    cfg = load_config()
    cfg.update({k: v for k, v in updates.items() if k in _DEFAULTS})
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
