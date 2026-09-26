# livetchat/server/routes_manifest.py
# Manifest d'auto-update. La version annoncée est celle de l'exe réellement déposé
# (fichier version.txt écrit à côté de l'exe par deploy/publish_client.sh), pas celle du code serveur.
import hashlib
import os

from fastapi import APIRouter, HTTPException

from livetchat.server import settings

router = APIRouter()

EXE_NAME = "TchatLive.exe"
EXE_PATH = os.path.join(settings.DOWNLOAD_DIR, EXE_NAME)
VERSION_PATH = os.path.join(settings.DOWNLOAD_DIR, "version.txt")
EXE_URL = f"{settings.PUBLIC_BASE_URL}/downloads/{EXE_NAME}"

_cache: dict = {"key": None, "sha256": None, "version": None}


def _release() -> tuple[str, str]:
    """(version, sha256) de l'exe publié, recalculés seulement si les fichiers changent."""
    exe, ver = os.stat(EXE_PATH), os.stat(VERSION_PATH)
    key = (exe.st_mtime_ns, exe.st_size, ver.st_mtime_ns)
    if _cache["key"] != key:
        h = hashlib.sha256()
        with open(EXE_PATH, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        with open(VERSION_PATH, encoding="utf-8") as f:
            version = f.read().strip()
        _cache.update(key=key, sha256=h.hexdigest(), version=version)
    return _cache["version"], _cache["sha256"]


@router.get("/manifest.json")
def manifest():
    if not (os.path.isfile(EXE_PATH) and os.path.isfile(VERSION_PATH)):
        raise HTTPException(status_code=404, detail="NO_RELEASE")
    version, sha256 = _release()
    if not version:
        raise HTTPException(status_code=404, detail="NO_RELEASE")
    return {"version": version, "url": EXE_URL, "sha256": sha256}
