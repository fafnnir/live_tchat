# livetchat/client/updater.py
# Mise à jour automatique de l'exe : manifest HTTPS, téléchargement vérifié par SHA-256,
# remplacement par un petit script .bat une fois l'appli fermée.
import hashlib
import os
import subprocess
import sys
import tempfile

import requests
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from livetchat.client.api import ApiError, fetch_manifest
from livetchat.client.config import API_BASE, TLS_VERIFY


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def is_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def _msg(parent, title: str, text: str, icon=QMessageBox.Icon.Information):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setIcon(icon)
    box.exec()


def check_update(current_version: str, parent=None, manifest: dict | None = None, silent: bool = False):
    """silent=True : ne dit rien s'il n'y a pas de nouvelle version ou en cas d'erreur."""
    try:
        m = manifest if manifest is not None else fetch_manifest()
        latest, url, sha = m.get("version"), m.get("url"), m.get("sha256")
        if not latest or not url or not sha:
            raise ApiError(0, "BAD_MANIFEST")
        # Ne télécharge que depuis notre serveur, en HTTPS
        if not url.startswith(f"{API_BASE}/downloads/") or not url.startswith("https://"):
            raise ApiError(0, "BAD_MANIFEST")
    except Exception as e:
        if not silent:
            _msg(parent, "Mise à jour", f"Impossible de vérifier : {e}", QMessageBox.Icon.Warning)
        return

    if not is_newer(latest, current_version):
        if not silent:
            _msg(parent, "À jour", f"Tu as la dernière version ({current_version}).")
        return

    if not getattr(sys, "frozen", False):
        _msg(parent, "Mise à jour", f"Version {latest} disponible (mise à jour auto seulement avec l'exe).")
        return

    box = QMessageBox(parent)
    box.setWindowTitle("Mise à jour disponible")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(f"Version actuelle : {current_version}\nNouvelle version : {latest}\n\nInstaller maintenant ?")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    if box.exec() == QMessageBox.StandardButton.Yes:
        _perform_update(url, sha, parent)


# Remplacement de l'exe (arguments : exe actuel, nouvel exe).
# - L'exe actuel est RENOMMÉ en .old (Windows l'autorise même s'il tourne encore), jamais supprimé
#   avant que le nouveau soit en place : si le déplacement échoue, l'ancien est remis.
# - Pas de delayed expansion (cassait les chemins contenant "!"), et "ping" pour attendre :
#   "timeout" échoue sans console (le script tourne en CREATE_NO_WINDOW).
_UPDATE_BAT = r"""@echo off
set "TARGET=%~1"
set "NEW=%~2"
set "OLD=%~1.old"
del /f /q "%OLD%" >nul 2>&1
set /a N=0
:rename
move /y "%TARGET%" "%OLD%" >nul 2>&1
if not errorlevel 1 goto place
set /a N+=1
if %N% GEQ 60 goto relaunch
ping -n 2 127.0.0.1 >nul
goto rename
:place
move /y "%NEW%" "%TARGET%" >nul 2>&1
if not errorlevel 1 goto relaunch
move /y "%OLD%" "%TARGET%" >nul 2>&1
:relaunch
start "" "%TARGET%"
set /a N=0
:cleanup
if not exist "%OLD%" goto end
del /f /q "%OLD%" >nul 2>&1
set /a N+=1
if %N% GEQ 60 goto end
ping -n 2 127.0.0.1 >nul
goto cleanup
:end
del "%~f0"
"""


def _bat_command(bat: str, *args: str) -> str:
    """Ligne de commande pour lancer le .bat. "cmd /s /c" ne retire que les guillemets extérieurs :
    les chemins avec espaces, "&" ou "!" restent intacts (avec "cmd /c" + plusieurs chemins entre
    guillemets, la commande était coupée et la mise à jour ne se relançait pas)."""
    return 'cmd /s /c ""' + bat + '" ' + " ".join(f'"{a}"' for a in args) + '"'


def _perform_update(url: str, sha256_hex: str, parent=None):
    tmp_dir = tempfile.gettempdir()
    new_path = os.path.join(tmp_dir, "TchatLive.new.exe")
    bat_path = os.path.join(tmp_dir, "tchat_update.bat")
    try:
        h = hashlib.sha256()
        with requests.get(url, stream=True, timeout=30, verify=TLS_VERIFY) as r:
            r.raise_for_status()
            with open(new_path, "wb") as f:
                for chunk in r.iter_content(128 * 1024):
                    if chunk:
                        f.write(chunk)
                        h.update(chunk)
        if h.hexdigest().lower() != sha256_hex.lower():
            os.remove(new_path)
            _msg(parent, "MAJ", "Fichier corrompu (hash invalide), mise à jour annulée.", QMessageBox.Icon.Critical)
            return
    except Exception as e:
        _msg(parent, "MAJ", f"Téléchargement impossible : {e}", QMessageBox.Icon.Critical)
        return

    batch = _UPDATE_BAT
    # Le nouvel exe ne doit pas hériter des variables internes de PyInstaller de celui-ci :
    # sinon il cherche python3xx.dll dans notre dossier _MEI temporaire, effacé à la fermeture
    # ("Failed to load Python DLL").
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(batch)
        subprocess.Popen(_bat_command(bat_path, sys.executable, new_path), close_fds=True,
                         env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        _msg(parent, "MAJ", f"Lancement de la mise à jour impossible : {e}", QMessageBox.Icon.Critical)
        return
    os._exit(0)
