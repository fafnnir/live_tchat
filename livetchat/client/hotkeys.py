# livetchat/client/hotkeys.py
# Raccourcis clavier globaux (façon bind Discord) : actifs même quand LiveTchat n'a pas le focus (en jeu…).
# Windows uniquement, via RegisterHotKey : pas de hook clavier, pas de dépendance en plus.
# Un raccourci est enregistré dans la config sous forme "mods:vk" (ex. "6:83" = Ctrl+Maj+S), "" = aucun.
import ctypes
import sys

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QPushButton, QWidget

AVAILABLE = sys.platform == "win32"

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312

# Touches dont le nom Windows doit être demandé avec le bit "étendu" (sinon on obtient celui du pavé numérique)
_EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90}
# Touches multimédia : Windows ne leur donne pas de nom
_VK_NAMES = {0xAD: "Muet", 0xAE: "Volume -", 0xAF: "Volume +", 0xB0: "Piste suivante",
             0xB1: "Piste précédente", 0xB2: "Stop média", 0xB3: "Lecture/Pause"}

if AVAILABLE:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    _user32.GetKeyNameTextW.argtypes = [wintypes.LONG, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetKeyNameTextW.restype = ctypes.c_int


def parse_bind(bind: str) -> tuple[int, int] | None:
    """ "mods:vk" -> (mods, vk), None si vide ou invalide."""
    try:
        mods, vk = (int(x) for x in bind.split(":"))
    except (ValueError, AttributeError):
        return None
    if not 0 < vk < 0xFF or mods & ~(MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_WIN):
        return None
    return mods, vk


def _mods_label(mods: int) -> str:
    parts = [name for flag, name in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Maj"), (MOD_WIN, "Win"))
             if mods & flag]
    return "".join(p + "+" for p in parts)


def _key_name(vk: int) -> str:
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    if AVAILABLE:
        scan = _user32.MapVirtualKeyW(vk, 0)
        if scan:
            buf = ctypes.create_unicode_buffer(64)
            lparam = (scan << 16) | ((1 << 24) if vk in _EXTENDED_VK else 0)
            if _user32.GetKeyNameTextW(lparam, buf, len(buf)):
                return buf.value.capitalize() if len(buf.value) > 1 else buf.value.upper()
    return f"Touche {vk}"


def bind_label(bind: str) -> str:
    parsed = parse_bind(bind)
    return _mods_label(parsed[0]) + _key_name(parsed[1]) if parsed else ""


# ──────────────────────────────────────────────────────────────
# Enregistrement auprès de Windows
# ──────────────────────────────────────────────────────────────
class GlobalHotkeys(QWidget):
    """Fenêtre native invisible qui reçoit les WM_HOTKEY. Émet triggered(nom de l'action)."""
    triggered = pyqtSignal(str)

    def __init__(self, actions: list[str]):
        super().__init__(None, Qt.WindowType.Tool)
        self._ids = {name: i + 1 for i, name in enumerate(actions)}
        self._names = {i: name for name, i in self._ids.items()}
        self._registered: set[int] = set()
        self._hwnd = int(self.winId()) if AVAILABLE else 0

    def apply(self, binds: dict[str, str]) -> dict[str, bool]:
        """Remplace tous les raccourcis. Renvoie, par action, False si Windows a refusé (déjà pris ailleurs)."""
        self.release()
        ok = {}
        for name, bind in binds.items():
            parsed = parse_bind(bind)
            if parsed is None or name not in self._ids:
                ok[name] = not bind
                continue
            hid = self._ids[name]
            if AVAILABLE and _user32.RegisterHotKey(self._hwnd, hid, parsed[0] | MOD_NOREPEAT, parsed[1]):
                self._registered.add(hid)
                ok[name] = True
            else:
                ok[name] = False
        return ok

    def release(self):
        for hid in self._registered:
            _user32.UnregisterHotKey(self._hwnd, hid)
        self._registered.clear()

    def nativeEvent(self, event_type, message):
        if AVAILABLE and bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self._names:
                self.triggered.emit(self._names[msg.wParam])
                return True, 0
        # Pas super().nativeEvent() : plante sous PyQt6 6.11 (access violation). False = message non traité.
        return False, 0


# ──────────────────────────────────────────────────────────────
# Bouton de saisie d'un raccourci
# ──────────────────────────────────────────────────────────────
class HotkeyButton(QPushButton):
    """Clic, puis appuyer sur la combinaison voulue. Échap annule."""
    capture_started = pyqtSignal()
    capture_finished = pyqtSignal(object)   # nouveau raccourci "mods:vk", ou None si annulé

    _MODIFIER_KEYS = {Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}

    def __init__(self, bind: str = ""):
        super().__init__()
        self.setObjectName("btn_hotkey")
        self._bind = bind
        self._capturing = False
        self.clicked.connect(self._start)
        self._refresh()

    def bind(self) -> str:
        return self._bind

    def set_bind(self, bind: str):
        self._bind = bind
        self._refresh()

    def set_error(self, error: bool):
        self.setProperty("error", error)
        self.style().unpolish(self)
        self.style().polish(self)

    def _refresh(self):
        self.setText(bind_label(self._bind) or "Aucun (clique pour choisir)")

    def _start(self):
        if self._capturing:
            return
        self._capturing = True
        self.setText("Appuie sur ta combinaison… (Échap = annuler)")
        self.grabKeyboard()
        self.capture_started.emit()

    def _finish(self, bind: str | None):
        self._capturing = False
        self.releaseKeyboard()
        if bind is not None:
            self._bind = bind
        self._refresh()
        self.capture_finished.emit(bind)

    def event(self, e):
        # Tab / Maj+Tab doivent pouvoir être bindés au lieu de changer de champ
        if self._capturing and e.type() == QEvent.Type.KeyPress:
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if not self._capturing:
            return super().keyPressEvent(e)
        mods = 0
        qmods = e.modifiers()
        if qmods & Qt.KeyboardModifier.ControlModifier:
            mods |= MOD_CONTROL
        if qmods & Qt.KeyboardModifier.AltModifier:
            mods |= MOD_ALT
        if qmods & Qt.KeyboardModifier.ShiftModifier:
            mods |= MOD_SHIFT
        if qmods & Qt.KeyboardModifier.MetaModifier:
            mods |= MOD_WIN
        if e.key() in self._MODIFIER_KEYS:
            self.setText(_mods_label(mods) + "…")
            return
        if e.key() == Qt.Key.Key_Escape and not mods:
            self._finish(None)
            return
        vk = e.nativeVirtualKey()
        if parse_bind(f"{mods}:{vk}"):
            self._finish(f"{mods}:{vk}")

    def keyReleaseEvent(self, e):
        if not self._capturing:
            super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        if self._capturing:
            self._finish(None)
        super().focusOutEvent(e)
