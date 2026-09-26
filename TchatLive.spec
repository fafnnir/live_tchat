# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    'websocket', 'PIL.ImageSequence',
    'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets',   # lecteur intégré (FFmpeg), pas de VLC
    'pillow_heif',                                       # photos iPhone (HEIC)
]
# Uniquement le client : le code serveur (fastapi, uvicorn) n'a rien à faire dans l'exe
hiddenimports += collect_submodules('livetchat.client') + collect_submodules('livetchat.shared')


a = Analysis(
    ['livetchat\\client\\__main__.py'],
    pathex=[],
    binaries=[],
    # Adresse du serveur + certificat épinglé (propres à chaque installation, non versionnés)
    datas=[('livetchat\\client\\server.json', 'livetchat\\client'),
           ('livetchat\\client\\server_cert.pem', 'livetchat\\client')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'fastapi', 'uvicorn', 'starlette', 'livetchat.server'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TchatLive',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,   # UPX fait souvent réagir les antivirus à tort
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
