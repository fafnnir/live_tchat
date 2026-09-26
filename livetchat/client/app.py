# livetchat/client/app.py
# Interface PyQt6 : salons + membres (façon Discord), envoi de médias, lecture en mémoire.
import sys
import traceback

from PyQt6.QtCore import QObject, QRunnable, Qt, QThread, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QStatusBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from livetchat.client import api
from livetchat.client.config import CLIENT_ID, load_config, save_config
from livetchat.client.media_overlay import MediaWindow
from livetchat.client.media_prep import FILE_FILTER, PrepError, kind_of, prepare, probe_duration
from livetchat.client.updater import check_update
from livetchat.client.ws_client import WsWorker
from livetchat.shared.version import VERSION

DEFAULT_IMAGE_S = 5.0
DEFAULT_AV_FALLBACK_S = 60.0

STYLE = """
QMainWindow, QWidget#central, QDialog { background-color: #313338; }
QLabel { color: #dbdee1; font-size: 13px; }
QLabel#section_title { color: #949ba4; font-size: 11px; font-weight: bold; }
QLabel#hint { color: #949ba4; font-size: 11px; }
QLabel#error { color: #f23f43; font-size: 12px; }
QLineEdit {
    background-color: #1e1f22; color: #dbdee1; border: 1px solid #1e1f22;
    border-radius: 6px; padding: 8px 12px; font-size: 13px; selection-background-color: #5865f2;
}
QLineEdit:focus { border: 1px solid #5865f2; }
QPushButton#btn_primary {
    background-color: #5865f2; color: white; border: none; border-radius: 6px;
    padding: 10px 20px; font-size: 14px; font-weight: bold;
}
QPushButton#btn_primary:hover { background-color: #4752c4; }
QPushButton#btn_primary:disabled { background-color: #3c4270; color: #9aa0c8; }
QPushButton#btn_secondary, QPushButton#btn_browse {
    background-color: #404249; color: #dbdee1; border: none; border-radius: 6px;
    padding: 8px 14px; font-size: 13px;
}
QPushButton#btn_secondary:hover, QPushButton#btn_browse:hover { background-color: #4e5058; }
QPushButton#btn_danger {
    background-color: #da373c; color: white; border: none; border-radius: 6px;
    padding: 8px 14px; font-size: 13px; font-weight: bold;
}
QPushButton#btn_danger:hover { background-color: #a12828; }
QFrame#card { background-color: #2b2d31; border-radius: 10px; }
QFrame#sidebar { background-color: #2b2d31; }
QTreeWidget {
    background-color: #2b2d31; color: #949ba4; border: none; font-size: 14px; outline: 0;
}
QTreeWidget::item { padding: 5px 4px; border-radius: 4px; }
QTreeWidget::item:hover { background-color: #35373c; color: #dbdee1; }
QTreeWidget::item:selected { background-color: #404249; color: white; }
QTreeWidget::branch { background: transparent; image: none; }
QCheckBox { color: #dbdee1; font-size: 13px; spacing: 8px; }
QStatusBar { background-color: #1e1f22; color: #949ba4; font-size: 12px; }
"""


# ──────────────────────────────────────────────────────────────
# Tâche de fond générique (réseau, conversion d'image…)
# ──────────────────────────────────────────────────────────────
class _TaskSignals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(object)


class Task(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _TaskSignals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            if not isinstance(e, (api.ApiError, PrepError)):
                traceback.print_exc()
            self.signals.failed.emit(e)
        else:
            self.signals.done.emit(result)


# ──────────────────────────────────────────────────────────────
# Connexion (mot de passe partagé)
# ──────────────────────────────────────────────────────────────
class LoginDialog(QDialog):
    def __init__(self, parent=None, message: str = ""):
        super().__init__(parent)
        self.token = ""
        self.setWindowTitle("LiveTchat — Connexion")
        self.setFixedWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)
        title = QLabel("🔒  Mot de passe du groupe")
        title.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        lay.addWidget(title)
        self._pw = QLineEdit()
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw.setPlaceholderText("Mot de passe partagé…")
        self._pw.returnPressed.connect(self._submit)
        lay.addWidget(self._pw)
        self._err = QLabel(message)
        self._err.setObjectName("error")
        self._err.setWordWrap(True)
        lay.addWidget(self._err)
        self._btn = QPushButton("Se connecter")
        self._btn.setObjectName("btn_primary")
        self._btn.clicked.connect(self._submit)
        lay.addWidget(self._btn)

    def _submit(self):
        pw = self._pw.text()
        if not pw:
            return
        self._btn.setEnabled(False)
        self._err.setText("Connexion…")
        task = Task(api.login, pw)
        task.signals.done.connect(self._ok)
        task.signals.failed.connect(self._ko)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def _ok(self, token: str):
        self.token = token
        self.accept()

    def _ko(self, err):
        self._btn.setEnabled(True)
        self._err.setText(str(err) if isinstance(err, api.ApiError) else f"Serveur injoignable : {err}")
        self._pw.selectAll()
        self._pw.setFocus()


def ask_login(parent=None, message: str = "") -> str:
    dlg = LoginDialog(parent, message)
    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.token:
        save_config(token=dlg.token)
        return dlg.token
    return ""


# ──────────────────────────────────────────────────────────────
# Fenêtre principale
# ──────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, token: str):
        super().__init__()
        cfg = load_config()
        self._token = token
        self._channel = cfg["channel"]
        self._tasks: set[Task] = set()           # garde les tâches en vie
        self._media_win: MediaWindow | None = None
        self._current: dict | None = None        # display_start en cours dans mon salon
        self._stopped_locally: set[str] = set()
        self._login_open = False

        self.setWindowTitle(f"LiveTchat {VERSION}")
        self.resize(760, 560)
        self.setMinimumSize(660, 500)
        self.setStyleSheet(STYLE)
        self._build_ui(cfg)
        self._start_ws(cfg)
        QTimer.singleShot(1500, self._auto_check_update)

    # ── UI ─────────────────────────────────────────────────────
    def _build_ui(self, cfg: dict):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Barre latérale : salons + membres
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 14, 10, 10)
        side.setSpacing(8)
        side.addWidget(self._section_label("Salons"))
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)
        self._tree.setRootIsDecorated(False)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.itemClicked.connect(self._on_tree_click)
        # Les salons viennent du serveur (message presence)
        self._channel_items: dict[str, QTreeWidgetItem] = {}
        self._channel_item(self._channel)
        side.addWidget(self._tree)
        root.addWidget(sidebar)

        # Partie droite
        right = QVBoxLayout()
        right.setContentsMargins(16, 14, 16, 12)
        right.setSpacing(12)

        self._title = QLabel()
        self._title.setStyleSheet("color: white; font-size: 20px; font-weight: bold;")
        right.addWidget(self._title)

        # Identité
        id_card = self._make_card()
        id_card.layout().addWidget(self._section_label("Identité"))
        row = QHBoxLayout()
        self._username_input = QLineEdit(cfg["username"])
        self._username_input.setPlaceholderText("Ton pseudo…")
        self._username_input.setMaxLength(24)
        self._username_input.returnPressed.connect(self._save_username)
        btn_save = QPushButton("Enregistrer")
        btn_save.setObjectName("btn_secondary")
        btn_save.clicked.connect(self._save_username)
        row.addWidget(self._username_input)
        row.addWidget(btn_save)
        id_card.layout().addLayout(row)
        right.addWidget(id_card)

        # Envoi
        send_card = self._make_card()
        lay = send_card.layout()
        lay.addWidget(self._section_label("Envoyer un média"))
        row = QHBoxLayout()
        row.addWidget(self._fixed_label("Durée (s) :"))
        self._duration_input = QLineEdit()
        self._duration_input.setFixedWidth(80)
        self._duration_input.setPlaceholderText("auto")
        row.addWidget(self._duration_input)
        hint = QLabel("auto = 5 s pour une image, en entier pour une vidéo / un son")
        hint.setObjectName("hint")
        row.addWidget(hint, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self._fixed_label("Texte :"))
        self._text_input = QLineEdit()
        self._text_input.setPlaceholderText("Message à afficher (optionnel)")
        self._text_input.setMaxLength(200)
        row.addWidget(self._text_input)
        lay.addLayout(row)
        row = QHBoxLayout()
        self._file_input = QLineEdit()
        self._file_input.setPlaceholderText("Aucun fichier sélectionné…")
        self._file_input.setReadOnly(True)
        btn_browse = QPushButton("📂  Parcourir")
        btn_browse.setObjectName("btn_browse")
        btn_browse.clicked.connect(self._browse)
        row.addWidget(self._file_input)
        row.addWidget(btn_browse)
        lay.addLayout(row)
        self._btn_send = QPushButton()
        self._btn_send.setObjectName("btn_primary")
        self._btn_send.setFixedHeight(42)
        self._btn_send.clicked.connect(self._send)
        lay.addWidget(self._btn_send)
        right.addWidget(send_card)

        # En cours
        now_card = self._make_card()
        lay = now_card.layout()
        lay.addWidget(self._section_label("En cours"))
        row = QHBoxLayout()
        self._now_label = QLabel("Rien pour le moment.")
        self._now_label.setWordWrap(True)
        row.addWidget(self._now_label, 1)
        self._btn_stop = QPushButton("⏹  Stop (chez moi)")
        self._btn_stop.setObjectName("btn_secondary")
        self._btn_stop.clicked.connect(self._stop_local)
        self._btn_skip = QPushButton("⏭  Passer pour tous")
        self._btn_skip.setObjectName("btn_danger")
        self._btn_skip.clicked.connect(self._skip_all)
        row.addWidget(self._btn_stop)
        row.addWidget(self._btn_skip)
        lay.addLayout(row)
        right.addWidget(now_card)

        right.addStretch()

        # Bas : case "par-dessus tout" + mise à jour
        row = QHBoxLayout()
        self._on_top = QCheckBox("Afficher les médias par-dessus tout")
        self._on_top.setChecked(cfg["on_top"])
        self._on_top.toggled.connect(lambda v: save_config(on_top=v))
        row.addWidget(self._on_top)
        row.addStretch()
        btn_update = QPushButton("🔄  Mise à jour")
        btn_update.setObjectName("btn_secondary")
        btn_update.clicked.connect(lambda: check_update(VERSION, self))
        row.addWidget(btn_update)
        right.addLayout(row)

        root.addLayout(right, 1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Connexion…")
        self._refresh_channel_ui()
        self._refresh_now_playing()

    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        return card

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_title")
        return lbl

    def _fixed_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(80)
        return lbl

    def _run(self, fn, *args, on_done=None, on_fail=None, **kwargs):
        task = Task(fn, *args, **kwargs)
        self._tasks.add(task)
        task.signals.done.connect(lambda r: (self._tasks.discard(task), on_done and on_done(r)))
        task.signals.failed.connect(lambda e: (self._tasks.discard(task), on_fail and on_fail(e)))
        QThreadPool.globalInstance().start(task)

    # ── WebSocket ──────────────────────────────────────────────
    def _start_ws(self, cfg: dict):
        self._ws_thread = QThread()
        self._ws = WsWorker(self._token, cfg["username"], self._channel)
        self._ws.moveToThread(self._ws_thread)
        self._ws_thread.started.connect(self._ws.run)
        self._ws.status_changed.connect(self._status.showMessage)
        self._ws.presence.connect(self._on_presence)
        self._ws.joined.connect(self._on_joined)
        self._ws.display_start.connect(self._on_display_start)
        self._ws.display_end.connect(self._on_display_end)
        self._ws.auth_error.connect(self._on_auth_error)
        self._ws_thread.start()

    def _on_auth_error(self):
        if self._login_open:
            return
        self._login_open = True
        token = ask_login(self, "Session expirée ou mot de passe changé, reconnecte-toi.")
        self._login_open = False
        if not token:
            self.close()
            return
        self._token = token
        self._ws.resume(token)

    def _channel_item(self, ch: str) -> QTreeWidgetItem:
        item = self._channel_items.get(ch)
        if item is None:
            item = QTreeWidgetItem([f"#  {ch}"])
            item.setData(0, Qt.ItemDataRole.UserRole, ch)
            self._tree.addTopLevelItem(item)
            item.setExpanded(True)
            self._channel_items[ch] = item
        return item

    def _on_presence(self, channels: list):
        ids = [str(info.get("id")) for info in channels if info.get("id")]
        for ch in list(self._channel_items):
            if ch not in ids and ch != self._channel:
                self._tree.takeTopLevelItem(self._tree.indexOfTopLevelItem(self._channel_items.pop(ch)))
        for info in channels:
            if not info.get("id"):
                continue
            item = self._channel_item(str(info["id"]))
            members = info.get("members", [])
            extra = []
            if info.get("playing"):
                extra.append("▶")
            if info.get("queue"):
                extra.append(f"{info['queue']} en attente")
            item.setText(0, f"#  {info['id']}" + (f"   {' · '.join(extra)}" if extra else ""))
            item.takeChildren()
            for name in members:
                child = QTreeWidgetItem([f"●  {name}"])
                child.setForeground(0, QBrush(QColor("#23a55a")))
                child.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.addChild(child)
            item.setExpanded(True)

    def _on_tree_click(self, item: QTreeWidgetItem, _col: int):
        ch = item.data(0, Qt.ItemDataRole.UserRole)
        if ch and ch != self._channel:
            self._channel = ch
            save_config(channel=ch)
            self._close_media()
            self._current = None
            self._refresh_now_playing()
            self._ws.join(ch)
            self._refresh_channel_ui()

    def _on_joined(self, ch: str):
        if ch != self._channel and self._channel in self._channel_items:
            # Salon inconnu du serveur : il nous a mis ailleurs
            old = self._channel_items.pop(self._channel)
            self._tree.takeTopLevelItem(self._tree.indexOfTopLevelItem(old))
        self._channel = ch
        save_config(channel=ch)
        self._channel_item(ch)
        self._refresh_channel_ui()

    def _refresh_channel_ui(self):
        for ch, item in self._channel_items.items():
            f = item.font(0)
            f.setBold(ch == self._channel)
            item.setFont(0, f)
            item.setForeground(0, QBrush(QColor("white" if ch == self._channel else "#949ba4")))
        self._tree.setCurrentItem(self._channel_items[self._channel])
        self._title.setText(f"#  {self._channel}")
        self._btn_send.setText(f"  Envoyer dans #{self._channel}")

    # ── Identité ───────────────────────────────────────────────
    def _username(self) -> str:
        return self._username_input.text().strip()[:24] or "guest"

    def _save_username(self):
        name = self._username()
        save_config(username=name)
        self._ws.rename(name)
        self._status.showMessage(f"✅  Pseudo enregistré : {name}")

    # ── Envoi ──────────────────────────────────────────────────
    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choisir un fichier", filter=FILE_FILTER)
        if path:
            self._file_input.setText(path)

    def _send(self):
        path = self._file_input.text().strip()
        if not path:
            self._status.showMessage("⚠️  Aucun fichier sélectionné.")
            return
        kind = kind_of(path)
        if not kind:
            self._status.showMessage("⚠️  Format non supporté.")
            return

        raw = self._duration_input.text().strip().replace(",", ".")
        try:
            dt = float(raw) if raw else 0.0
        except ValueError:
            self._status.showMessage("⚠️  Durée invalide (un nombre de secondes, ou vide pour auto).")
            return
        if dt <= 0:
            if kind == "image":
                dt = DEFAULT_IMAGE_S
            else:
                self._status.showMessage("Lecture de la durée…")
                QApplication.processEvents()
                dt = probe_duration(path) or DEFAULT_AV_FALLBACK_S
                dt += 0.5   # marge pour que la fin ne soit pas coupée

        uname, text, channel = self._username(), self._text_input.text(), self._channel
        save_config(username=uname)
        self._btn_send.setEnabled(False)
        self._status.showMessage("Préparation…" if kind == "image" else "Envoi…")

        def work():
            data, filename, _ = prepare(path)
            return api.upload(self._token, data, filename, display_time=dt, display_text=text,
                              username=uname, channel=channel)

        self._run(work, on_done=self._upload_ok, on_fail=self._upload_ko)

    def _upload_ok(self, info: dict):
        self._btn_send.setEnabled(True)
        pos = info.get("queue_position", "?")
        self._status.showMessage(f"✅  Envoyé — position dans la file : {pos}")
        self._file_input.clear()
        self._text_input.clear()

    def _upload_ko(self, err):
        self._btn_send.setEnabled(True)
        if isinstance(err, api.ApiError) and err.status == 401:
            self._on_auth_error()
            return
        if isinstance(err, (api.ApiError, PrepError)):
            self._status.showMessage(f"❌  {err}")
        else:
            self._status.showMessage(f"❌  Serveur injoignable : {err}")

    # ── Réception ──────────────────────────────────────────────
    def _on_display_start(self, msg: dict):
        if msg.get("channel") != self._channel:
            return
        self._close_media()
        self._current = msg
        self._refresh_now_playing()
        media_id = msg["media_id"]
        self._run(
            api.fetch_media, self._token, msg["url"],
            on_done=lambda data: self._show_media(msg, data),
            on_fail=lambda e: self._current is msg and self._status.showMessage(f"❌  Média : {e}"),
        )

    def _show_media(self, msg: dict, data: bytes):
        # Arrivé trop tard (fini, passé, ou stoppé chez moi) : on n'affiche pas
        if self._current is not msg or msg["media_id"] in self._stopped_locally:
            return
        win = MediaWindow(
            media_id=msg["media_id"], kind=msg["kind"], data=data, content_type=msg.get("content_type", ""),
            caption=msg.get("display_text", ""), username=msg.get("username", "guest"),
            duration_s=float(msg.get("display_time", 5)), on_top=self._on_top.isChecked(),
        )
        win.closed.connect(self._on_media_closed)
        self._media_win = win

    def _on_display_end(self, msg: dict):
        if self._current and self._current.get("media_id") == msg.get("media_id"):
            self._current = None
            if self._media_win:
                self._media_win.finish()
            self._refresh_now_playing()

    def _on_media_closed(self, media_id: str):
        if self._media_win and self._media_win.media_id == media_id:
            self._media_win = None
        # Fermé avant la fin côté serveur (clic ou Stop) = arrêté chez moi
        if self._current and self._current.get("media_id") == media_id:
            self._stopped_locally.add(media_id)
        self._refresh_now_playing()

    def _close_media(self):
        if self._media_win:
            win, self._media_win = self._media_win, None
            win.close()

    def _stop_local(self):
        if self._current:
            self._stopped_locally.add(self._current["media_id"])
        self._close_media()
        self._refresh_now_playing()

    def _skip_all(self):
        if self._current and self._current.get("sender_id") == CLIENT_ID:
            self._ws.skip(self._current["media_id"])

    def _refresh_now_playing(self):
        cur = self._current
        if not cur:
            self._now_label.setText("Rien pour le moment.")
            self._btn_stop.setVisible(False)
            self._btn_skip.setVisible(False)
            return
        icon = {"image": "🖼", "video": "🎬", "audio": "🎵"}.get(cur.get("kind"), "📦")
        mine = cur.get("sender_id") == CLIENT_ID
        stopped = cur["media_id"] in self._stopped_locally
        who = "toi" if mine else cur.get("username", "?")
        self._now_label.setText(f"{icon}  {cur.get('kind')} de {who}" + ("  (arrêté chez moi)" if stopped else ""))
        self._btn_stop.setVisible(not stopped)
        self._btn_skip.setVisible(mine)

    # ── Mise à jour ────────────────────────────────────────────
    def _auto_check_update(self):
        self._run(api.fetch_manifest, on_done=lambda m: check_update(VERSION, self, manifest=m, silent=True))

    def closeEvent(self, event):
        self._close_media()
        self._ws.stop()
        self._ws_thread.quit()
        self._ws_thread.wait(2000)
        super().closeEvent(event)


# ──────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#313338"), (QPalette.ColorRole.WindowText, "#dbdee1"),
        (QPalette.ColorRole.Base, "#1e1f22"), (QPalette.ColorRole.Text, "#dbdee1"),
        (QPalette.ColorRole.Button, "#404249"), (QPalette.ColorRole.ButtonText, "#dbdee1"),
        (QPalette.ColorRole.Highlight, "#5865f2"), (QPalette.ColorRole.HighlightedText, "white"),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)

    token = load_config()["token"] or ask_login()
    if not token:
        sys.exit(0)
    win = MainWindow(token)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
