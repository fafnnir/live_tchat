# livetchat/client/media_overlay.py
# Fenêtre d'affichage d'un média. Tout est lu depuis la MÉMOIRE (QBuffer) : aucun fichier
# temporaire, et le lecteur intégré à Qt (FFmpeg) remplace VLC.
#   on_top=True  : sans bordure, au-dessus de tout (jeux compris), ne vole pas le focus
#   on_top=False : fenêtre normale, qui passe derrière les autres
# Un clic sur le média l'arrête (chez soi uniquement).
from PyQt6.QtCore import (
    QBuffer, QByteArray, QIODevice, QPropertyAnimation, QEasingCurve, QSize, Qt, QTimer, QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QGuiApplication, QImageReader, QMovie, QPixmap, QScreen
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

_CAPTION_CSS = "background: black; color: white; font-size: 26px; font-weight: bold; padding: 10px;"
_HEADER_CSS = "background: black; color: #dbdee1; font-size: 18px; font-weight: bold; padding: 6px 12px;"

# Suffixes donnés au lecteur pour qu'il devine le format du flux en mémoire
_HINTS = {
    "video/mp4": "media.mp4", "video/quicktime": "media.mov", "video/webm": "media.webm",
    "video/x-matroska": "media.mkv", "audio/mpeg": "media.mp3", "audio/wav": "media.wav",
    "audio/ogg": "media.ogg", "audio/flac": "media.flac", "audio/mp4": "media.m4a", "audio/aac": "media.aac",
}


def _buffer(data: bytes, parent) -> QBuffer:
    buf = QBuffer(parent)
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    return buf


class MediaWindow(QWidget):
    closed = pyqtSignal(str)   # media_id

    def __init__(self, *, media_id: str, kind: str, data: bytes, content_type: str,
                 caption: str, username: str, duration_s: float, on_top: bool,
                 screen: QScreen | None = None):
        super().__init__(None)
        target = screen or QApplication.primaryScreen()
        self.setScreen(target)
        self.media_id = media_id
        self._player: QMediaPlayer | None = None
        self._closing = False

        if on_top:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
            )
        else:
            self.setWindowFlags(Qt.WindowType.Window)
        self.setWindowTitle(f"LiveTchat — {username}")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)   # ne coupe pas le jeu en cours
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet("background: black;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Clic = arrêter chez moi")

        screen = target.availableGeometry()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Texte venant des autres membres : toujours en texte brut (sinon QLabel interprète le HTML,
        # ex. <img src="file://..."> qui ferait fuiter le hash Windows via SMB)
        header = QLabel(f"👤  {username}")
        header.setTextFormat(Qt.TextFormat.PlainText)
        header.setStyleSheet(_HEADER_CSS)
        layout.addWidget(header)

        if kind == "image":
            self._build_image(layout, data, QSize(int(screen.width() * 0.85), int(screen.height() * 0.75)))
        elif kind == "video":
            self._build_player(layout, data, content_type, video_size=QSize(int(screen.width() * 0.7), int(screen.height() * 0.6)))
        else:
            self._build_player(layout, data, content_type, video_size=None, caption_fallback=not caption)

        if caption:
            cap = QLabel(caption)
            cap.setTextFormat(Qt.TextFormat.PlainText)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setWordWrap(True)
            cap.setStyleSheet(_CAPTION_CSS)
            layout.addWidget(cap)

        if kind == "audio":
            self.setFixedWidth(int(screen.width() * 0.5))
        self.adjustSize()
        self.move(screen.x() + (screen.width() - self.width()) // 2,
                  screen.y() + (screen.height() - self.height()) // 2)

        # Filet de sécurité si la fin n'arrive pas du serveur
        QTimer.singleShot(int((duration_s + 5) * 1000), self.finish)
        self.show()

    # ── Construction ─────────────────────────────────────────
    def _build_image(self, layout, data: bytes, max_size: QSize):
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        reader = QImageReader(_buffer(data, self))
        size = reader.size()
        animated = reader.supportsAnimation() and reader.imageCount() != 1
        if animated and size.isValid():
            # GIF / WebP animé : QMovie lit directement le buffer en mémoire
            movie = QMovie(_buffer(data, self), QByteArray(), self)
            movie.setScaledSize(size.scaled(max_size, Qt.AspectRatioMode.KeepAspectRatio)
                                if size.width() > max_size.width() or size.height() > max_size.height() else size)
            movie.setCacheMode(QMovie.CacheMode.CacheNone)
            lbl.setMovie(movie)
            movie.start()
            self._movie = movie
        else:
            px = QPixmap()
            px.loadFromData(data)
            if px.width() > max_size.width() or px.height() > max_size.height():
                px = px.scaled(max_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl.setPixmap(px)
        layout.addWidget(lbl)

    def _build_player(self, layout, data: bytes, content_type: str, *, video_size: QSize | None,
                      caption_fallback: bool = False):
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        if video_size:
            video = QVideoWidget()
            video.setFixedSize(video_size)
            layout.addWidget(video)
            self._player.setVideoOutput(video)
        elif caption_fallback:
            info = QLabel("🎵  Son en cours…")
            info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            info.setStyleSheet(_CAPTION_CSS)
            layout.addWidget(info)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.setSourceDevice(_buffer(data, self), QUrl(_HINTS.get(content_type, "media.bin")))
        self._player.play()

    def _on_status(self, status):
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            self.finish()

    # ── Fin ──────────────────────────────────────────────────
    def finish(self):
        """Fin normale : fondu puis fermeture."""
        if self._closing:
            return
        self._closing = True
        if self._player:
            self._player.pause()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(350)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self.close)
        anim.start()
        self._anim = anim

    def mousePressEvent(self, event):
        self.close()

    def closeEvent(self, event):
        self._closing = True
        if self._player:
            self._player.stop()
            self._player.setSourceDevice(None)
        self.closed.emit(self.media_id)
        super().closeEvent(event)


# ─────────────────────────────────────────────
# Écrans
# ─────────────────────────────────────────────
def screen_label(index: int, screen: QScreen) -> str:
    size = screen.size()
    primary = "  (principal)" if screen == QGuiApplication.primaryScreen() else ""
    return f"Écran {index + 1} — {size.width()}×{size.height()}{primary}"


def find_screen(name: str) -> QScreen | None:
    """Écran enregistré par son nom ; None s'il n'est plus branché (= écran principal)."""
    return next((s for s in QGuiApplication.screens() if s.name() == name), None) if name else None


def identify_screens(duration_ms: int = 2500) -> list[QWidget]:
    """Affiche un gros numéro au centre de chaque écran pendant quelques secondes."""
    windows = []
    for i, screen in enumerate(QGuiApplication.screens()):
        w = QLabel(str(i + 1))
        w.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        w.setStyleSheet("background: #5865f2; color: white; font-size: 120px; font-weight: bold; border-radius: 20px;")
        w.setFixedSize(220, 220)
        w.setScreen(screen)
        geo = screen.geometry()
        w.move(geo.x() + (geo.width() - 220) // 2, geo.y() + (geo.height() - 220) // 2)
        w.show()
        QTimer.singleShot(duration_ms, w.close)
        windows.append(w)
    return windows
