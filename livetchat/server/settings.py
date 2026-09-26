# livetchat/server/settings.py
# Toute la config passe par des variables d'environnement (fichier .env chargé par systemd).
import os


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


HOST = _env("LTCHAT_HOST", "127.0.0.1")          # derrière nginx : jamais exposé directement
PORT = int(_env("LTCHAT_PORT", "8001"))            # nginx écoute sur 8000 (HTTPS) et relaie ici

# Adresse vue par les clients, ex. https://1.2.3.4:8000 (sert au lien de mise à jour)
PUBLIC_BASE_URL = _env("LTCHAT_PUBLIC_URL", "https://127.0.0.1:8000").rstrip("/")

# Dossiers
MEDIA_DIR = _env("LTCHAT_MEDIA_DIR", "/var/lib/livetchat/media")
DOWNLOAD_DIR = _env("LTCHAT_DOWNLOAD_DIR", "/var/lib/livetchat/downloads")

# Secrets
SECRET_KEY = _env("LTCHAT_SECRET", "")               # clé HMAC (jetons de session et liens média)
PASSWORD_HASH = _env("LTCHAT_PASSWORD_HASH", "")     # scrypt, voir server/set_password.py

# nginx sert les fichiers (X-Accel-Redirect). Mettre 0 en local sans nginx.
USE_X_ACCEL = _env("LTCHAT_X_ACCEL", "1") == "1"
X_ACCEL_PREFIX = "/_protected_media/"

# Salons (le client reçoit la liste du serveur)
ALLOWED_CHANNELS = tuple(c.strip() for c in _env("LTCHAT_CHANNELS", "general,gaming,chill").split(",") if c.strip())

# Limites (le serveur a peu de ressources)
MAX_BYTES = {
    "image": 10 * 1024 * 1024,
    "video": 25 * 1024 * 1024,
    "audio": 15 * 1024 * 1024,
}
MAX_DISPLAY_S = {"image": 60.0, "video": 300.0, "audio": 300.0}
MIN_DISPLAY_S = 1.0
MAX_QUEUE_PER_CHANNEL = 10
MAX_QUEUED_PER_IP = 3          # une seule personne ne peut pas bloquer un salon
MAX_TEXT_LEN = 200
MAX_USERNAME_LEN = 24
MAX_WS_CLIENTS = 100
UPLOADS_PER_MINUTE = 10         # par IP
LOGIN_MAX_FAILS = 5             # par IP, avant blocage
LOGIN_LOCK_S = 15 * 60
SESSION_TTL_S = 30 * 24 * 3600  # jeton de session : 30 jours
MEDIA_LINK_GRACE_S = 30         # un lien média reste valide display_time + 30 s
MEDIA_DELETE_AFTER_S = 60       # fichier effacé 60 s après la fin d'affichage
