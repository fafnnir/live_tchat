# livetchat/server/security.py
# Mot de passe partagé (scrypt), jetons signés HMAC, limiteurs de débit en mémoire.
import base64
import hashlib
import hmac
import secrets
import time
import unicodedata

from livetchat.server import settings

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1   # ~16 Mo de RAM par vérification

_SECRET = (settings.SECRET_KEY or secrets.token_hex(32)).encode()
# Changer le mot de passe invalide tous les jetons existants
_SESSION_KEY = hashlib.sha256(_SECRET + b"|session|" + settings.PASSWORD_HASH.encode()).digest()
_MEDIA_KEY = hashlib.sha256(_SECRET + b"|media").digest()


# ------------------------------------------------------------
# Mot de passe
# ------------------------------------------------------------
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    # ':' plutôt que '$' pour ne pas gêner les fichiers .env
    return f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{_b64e(salt)}:{_b64e(dk)}"


def verify_password(password: str) -> bool:
    try:
        algo, n, r, p, salt, expected = settings.PASSWORD_HASH.split(":")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode()[:256], salt=_b64d(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, _b64d(expected))
    except Exception:
        return False


# ------------------------------------------------------------
# Jetons signés
# ------------------------------------------------------------
def _sign(key: bytes, payload: str) -> str:
    return _b64e(hmac.new(key, payload.encode(), hashlib.sha256).digest()[:24])


def make_session_token() -> str:
    payload = f"s:{secrets.token_hex(8)}:{int(time.time()) + settings.SESSION_TTL_S}"
    return f"{_b64e(payload.encode())}.{_sign(_SESSION_KEY, payload)}"


def check_session_token(token) -> bool:
    if not isinstance(token, str) or not token or len(token) > 200 or "." not in token:
        return False
    try:
        p64, sig = token.split(".", 1)
        payload = _b64d(p64).decode()
        if not hmac.compare_digest(sig, _sign(_SESSION_KEY, payload)):
            return False
        kind, _sid, exp = payload.split(":")
        return kind == "s" and int(exp) > time.time()
    except Exception:
        return False


def media_signature(media_id: str, exp: int) -> str:
    return _sign(_MEDIA_KEY, f"m:{media_id}:{exp}")


def check_media_signature(media_id: str, exp: int, sig: str) -> bool:
    if exp <= time.time() or not sig.isascii():   # compare_digest refuse les str non-ASCII
        return False
    return hmac.compare_digest(sig, media_signature(media_id, exp))


def secret_problem() -> str | None:
    """Raison de refuser de démarrer si la clé secrète est absente ou reste celle d'exemple."""
    s = settings.SECRET_KEY
    if not s:
        return "LTCHAT_SECRET absent"
    if "REMPLACER" in s or len(s) < 32:
        return "LTCHAT_SECRET trop court ou encore la valeur d'exemple (32 caractères minimum)"
    return None


# ------------------------------------------------------------
# Nettoyage des entrées utilisateur
# ------------------------------------------------------------
def clean_text(value: str | None, max_len: int) -> str:
    if not value:
        return ""
    # Caractères de contrôle / invisibles (retours ligne compris) -> espace
    out = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in value)
    return " ".join(out.split())[:max_len]


def clean_username(value: str | None) -> str:
    return clean_text(value, settings.MAX_USERNAME_LEN) or "guest"


# ------------------------------------------------------------
# Limiteurs de débit (mémoire, purgés au fil de l'eau)
# ------------------------------------------------------------
class RateLimiter:
    """Au plus `limit` événements par `window` secondes et par clé."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > 5000:  # borne mémoire
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
        return True


class LoginGuard:
    """Bloque une IP après trop d'échecs de mot de passe."""

    def __init__(self, max_fails: int, lock_s: float):
        self.max_fails, self.lock_s = max_fails, lock_s
        self._fails: dict[str, tuple[int, float]] = {}

    def is_locked(self, ip: str) -> bool:
        count, since = self._fails.get(ip, (0, 0.0))
        if count >= self.max_fails and time.monotonic() - since < self.lock_s:
            return True
        if count >= self.max_fails:
            self._fails.pop(ip, None)
        return False

    def fail(self, ip: str):
        count, _ = self._fails.get(ip, (0, 0.0))
        self._fails[ip] = (count + 1, time.monotonic())
        if len(self._fails) > 5000:
            self._fails.clear()

    def success(self, ip: str):
        self._fails.pop(ip, None)
