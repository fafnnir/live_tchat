#!/usr/bin/env bash
# Publie l'exe du client pour la mise à jour automatique.
# Usage (Git Bash, depuis le dossier qui contient livetchat/ et TchatLive.spec) :
#   py -m PyInstaller --noconfirm --clean --distpath dist_<version> TchatLive.spec
#   bash livetchat/deploy/publish_client.sh <serveur-ssh>        (ex. ubuntu@1.2.3.4)
#
# L'exe est déposé d'abord, la version ensuite : les clients ne voient jamais une version
# annoncée dont l'exe n'est pas encore là. Le code serveur n'a pas besoin d'être mis à jour.
set -euo pipefail

SERVER="${1:?usage : publish_client.sh <serveur-ssh>}"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' livetchat/shared/version.py | tr -d '\r')"
EXE="dist_$VERSION/TchatLive.exe"
DEST=/var/lib/livetchat/downloads

[ -n "$VERSION" ] || { echo "Version introuvable dans livetchat/shared/version.py"; exit 1; }
[ -f "$EXE" ] || { echo "$EXE absent. Build : py -m PyInstaller --noconfirm --clean --distpath dist_$VERSION TchatLive.spec"; exit 1; }

scp -q "$EXE" "$SERVER:$DEST/TchatLive.exe.part"
ssh "$SERVER" "set -e; cd $DEST
chmod 640 TchatLive.exe.part && mv TchatLive.exe.part TchatLive.exe
echo '$VERSION' > version.txt.part && chmod 640 version.txt.part && mv version.txt.part version.txt"
echo "Publié : TchatLive.exe $VERSION"
