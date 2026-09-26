# livetchat/server/set_password.py
# Usage (sur le serveur) : venv/bin/python -m livetchat.server.set_password
# Écrit le hash du mot de passe partagé dans le fichier .env, puis il faut redémarrer :
#   sudo systemctl restart livetchat
import getpass
import os
import sys

from livetchat.server.security import hash_password

ENV_PATH = os.environ.get("LTCHAT_ENV_FILE", "/home/ubuntu/livetchat/.env")


def main():
    pw = getpass.getpass("Nouveau mot de passe partagé : ")
    if len(pw) < 8:
        sys.exit("Minimum 8 caractères.")
    if pw != getpass.getpass("Confirmer : "):
        sys.exit("Les deux saisies ne correspondent pas.")

    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if not l.startswith("LTCHAT_PASSWORD_HASH=")]
    lines.append(f"LTCHAT_PASSWORD_HASH={hash_password(pw)}")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, 0o600)
    print(f"OK, écrit dans {ENV_PATH}. Redémarre : sudo systemctl restart livetchat")


if __name__ == "__main__":
    main()
