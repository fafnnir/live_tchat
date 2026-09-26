# livetchat/client/media_prep.py
# Préparation d'un fichier avant envoi : type, conversion/compression des images (sur le PC,
# pas sur le serveur), durée des vidéos/sons.
import io
from pathlib import Path

from PIL import Image, ImageOps

try:  # photos iPhone (HEIC/HEIF)
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

IMAGE_NATIVE = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
IMAGE_CONVERT = {".jfif", ".bmp", ".tif", ".tiff", ".ico", ".avif", ".heic", ".heif", ".tga", ".dib"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac"}
ALL_EXTS = IMAGE_NATIVE | IMAGE_CONVERT | VIDEO_EXTS | AUDIO_EXTS

MAX_BYTES = {"image": 10 * 1024 * 1024, "video": 25 * 1024 * 1024, "audio": 15 * 1024 * 1024}
MAX_SIDE = 1920                 # au-delà, l'image est réduite avant l'envoi
KEEP_AS_IS_BYTES = 2 * 1024 * 1024

FILE_FILTER = "Médias (" + " ".join(f"*{e}" for e in sorted(ALL_EXTS)) + ")"


class PrepError(Exception):
    pass


def kind_of(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_NATIVE or ext in IMAGE_CONVERT:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def prepare(path: str) -> tuple[bytes, str, str]:
    """Renvoie (octets, nom de fichier à envoyer, kind). Lève PrepError."""
    p = Path(path)
    kind = kind_of(path)
    if not kind:
        raise PrepError(f"Format non supporté : {p.suffix or '?'}")
    if kind == "image":
        data, name = _prepare_image(p)
    else:
        data, name = p.read_bytes(), p.name
    limit = MAX_BYTES[kind]
    if len(data) > limit:
        raise PrepError(f"Fichier trop lourd : {len(data) / 1048576:.1f} Mo (max {limit // 1048576} Mo)")
    return data, name, kind


def _prepare_image(p: Path) -> tuple[bytes, str]:
    raw = p.read_bytes()
    ext = p.suffix.lower()
    try:
        img = Image.open(io.BytesIO(raw))
        animated = getattr(img, "is_animated", False)
        width, height = img.size
    except Exception:
        raise PrepError("Image illisible (format non reconnu ou fichier abîmé).")

    # GIF / WebP animés : envoyés tels quels (les réencoder coûterait cher et dégraderait)
    if animated and ext in (".gif", ".webp"):
        return raw, p.name

    small = max(width, height) <= MAX_SIDE and len(raw) <= KEEP_AS_IS_BYTES
    if ext in IMAGE_NATIVE and small:
        return raw, p.name

    # Conversion en WebP : rotation EXIF appliquée, taille réduite, transparence gardée
    try:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
        out = io.BytesIO()
        img.save(out, "WEBP", quality=85, method=4)
    except Exception as e:
        raise PrepError(f"Conversion de l'image impossible : {e}")
    return out.getvalue(), p.stem[:60] + ".webp"


def probe_duration(path: str, timeout_ms: int = 5000) -> float | None:
    """Durée d'une vidéo/d'un son en secondes (lecteur Qt, dans le thread de l'UI)."""
    from PyQt6.QtCore import QEventLoop, QTimer, QUrl
    from PyQt6.QtMultimedia import QMediaPlayer

    player = QMediaPlayer()
    loop = QEventLoop()
    result: dict = {}

    def on_duration(ms: int):
        if ms > 0:
            result["ms"] = ms
            loop.quit()

    def on_status(status):
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            loop.quit()

    player.durationChanged.connect(on_duration)
    player.mediaStatusChanged.connect(on_status)
    QTimer.singleShot(timeout_ms, loop.quit)
    player.setSource(QUrl.fromLocalFile(path))
    if player.duration() > 0:
        result["ms"] = player.duration()
    else:
        loop.exec()
    player.setSource(QUrl())
    player.deleteLater()
    return result["ms"] / 1000 if "ms" in result else None
