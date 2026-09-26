# livetchat/server/routes_manifest.py
# Manifest d'auto-update. Le hash de l'exe est mis en cache tant que le fichier ne change pas.
import hashlib
import os

from fastapi import APIRouter, HTTPException

from livetchat.server import settings
from livetchat.shared.version import VERSION

router = APIRouter()

EXE_NAME = "TchatLive.exe"
EXE_PATH = os.path.join(settings.DOWNLOAD_DIR, EXE_NAME)
EXE_URL = f"{settings.PUBLIC_BASE_URL}/downloads/{EXE_NAME}"

_cache: dict = {"key": None, "sha256": None}


def _sha256_cached(path: str) -> str:
    st = os.stat(path)
    key = (st.st_mtime_ns, st.st_size)
    if _cache["key"] != key:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        _cache.update(key=key, sha256=h.hexdigest())
    return _cache["sha256"]


@router.get("/manifest.json")
def manifest():
    if not os.path.isfile(EXE_PATH):
        raise HTTPException(status_code=404, detail="NO_RELEASE")
    return {
        "version": VERSION,
        "url": EXE_URL,
        "sha256": _sha256_cached(EXE_PATH),
        "notes": "Salons avec membres, lecture sans fichier sur disque, HTTPS, mot de passe.",
    }
