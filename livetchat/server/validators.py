# livetchat/server/validators.py
# Vérifie le type réel d'un fichier à partir de ses premiers octets (pas seulement l'extension).
# Les formats d'image "exotiques" (heic, avif, bmp, tiff...) sont convertis en WebP par le client.

# extension -> (kind, content-type, famille de signature)
FORMATS = {
    ".jpg":  ("image", "image/jpeg", "jpeg"),
    ".jpeg": ("image", "image/jpeg", "jpeg"),
    ".png":  ("image", "image/png", "png"),
    ".gif":  ("image", "image/gif", "gif"),
    ".webp": ("image", "image/webp", "webp"),
    ".mp4":  ("video", "video/mp4", "isobmff"),
    ".m4v":  ("video", "video/mp4", "isobmff"),
    ".mov":  ("video", "video/quicktime", "isobmff"),
    ".webm": ("video", "video/webm", "ebml"),
    ".mkv":  ("video", "video/x-matroska", "ebml"),
    ".mp3":  ("audio", "audio/mpeg", "mp3"),
    ".wav":  ("audio", "audio/wav", "wav"),
    ".ogg":  ("audio", "audio/ogg", "ogg"),
    ".oga":  ("audio", "audio/ogg", "ogg"),
    ".opus": ("audio", "audio/ogg", "ogg"),
    ".flac": ("audio", "audio/flac", "flac"),
    ".m4a":  ("audio", "audio/mp4", "isobmff"),
    ".aac":  ("audio", "audio/aac", "adts"),
}


def _is_mpeg_frame(p: bytes) -> bool:
    return len(p) >= 2 and p[0] == 0xFF and (p[1] & 0xE0) == 0xE0


def _matches(family: str, p: bytes) -> bool:
    if family == "jpeg":
        return p.startswith(b"\xff\xd8\xff")
    if family == "png":
        return p.startswith(b"\x89PNG\r\n\x1a\n")
    if family == "gif":
        return p.startswith((b"GIF87a", b"GIF89a"))
    if family == "webp":
        return len(p) >= 12 and p.startswith(b"RIFF") and p[8:12] == b"WEBP"
    if family == "isobmff":
        return len(p) >= 12 and p[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")
    if family == "ebml":
        return p.startswith(b"\x1a\x45\xdf\xa3")
    if family == "mp3":
        return p.startswith(b"ID3") or _is_mpeg_frame(p)
    if family == "wav":
        return len(p) >= 12 and p.startswith(b"RIFF") and p[8:12] == b"WAVE"
    if family == "ogg":
        return p.startswith(b"OggS")
    if family == "flac":
        return p.startswith(b"fLaC") or (p.startswith(b"ID3") and b"fLaC" in p[:4096])
    if family == "adts":
        return p.startswith(b"ID3") or (len(p) >= 2 and p[0] == 0xFF and (p[1] & 0xF6) == 0xF0)
    return False


def detect(ext: str, prefix: bytes) -> tuple[str, str] | None:
    """Renvoie (kind, content_type) si l'extension est acceptée ET correspond au contenu."""
    fmt = FORMATS.get(ext.lower())
    if not fmt:
        return None
    kind, content_type, family = fmt
    return (kind, content_type) if _matches(family, prefix) else None
