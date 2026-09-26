# LiveTchat

Partage de médias **en direct** entre amis, façon alertes de stream : quelqu'un envoie une image,
un GIF, une vidéo ou un son, et ça s'affiche en même temps chez tous ceux qui sont dans le même
salon — par-dessus le jeu en cours si on veut.

- Salons avec la liste des membres connectés (comme Discord)
- File d'attente par salon : les médias passent un par un
- **Stop** pour arrêter chez soi, **Passer pour tous** pour l'expéditeur
- Case *Afficher les médias par-dessus tout* (sinon fenêtre normale), sans voler le focus du jeu
- Choix de l'**écran** d'affichage (bouton *Identifier* pour savoir lequel est lequel)
- Lecture **en mémoire uniquement** : rien n'est enregistré sur le PC des participants, pas besoin de VLC
- Images : jpg, png, gif, webp (animés compris), bmp, tiff, ico, avif, heic (iPhone)… converties et
  réduites sur le PC de l'expéditeur avant l'envoi
- Vidéos : mp4, mov, webm, mkv — Sons : mp3, wav, ogg, opus, flac, m4a, aac
- Durée *auto* : 5 s pour une image, en entier pour une vidéo ou un son
- Groupe protégé par un mot de passe partagé, tout passe en HTTPS
- Mise à jour automatique du client (.exe)

Inspiration : CCB

---

## Comment ça marche

```
 PC (TchatLive.exe) ──HTTPS/WSS :8000──▶ nginx ──▶ appli FastAPI (127.0.0.1:8001)
                                          │
                                          └─ envoie lui-même les médias (liens signés, temporaires)
```

- **Serveur** (`livetchat/server`) : FastAPI + uvicorn, derrière nginx. Pensé pour une petite VM
  (testé sur une VM Oracle Cloud gratuite, 1 Go de RAM : l'appli consomme ~50 Mo).
- **Client** (`livetchat/client`) : application Windows PyQt6, compilée en `TchatLive.exe` avec PyInstaller.
- **HTTPS sans nom de domaine** : le serveur génère un certificat auto-signé, et ce certificat est
  intégré dans l'exe (*épinglage*). Le client ne parle qu'à *votre* serveur.

Sécurité en bref : mot de passe partagé haché (scrypt) et blocage après 5 essais ratés, jetons de
session signés, liens de média signés qui expirent après l'affichage, vérification du vrai type des
fichiers, noms de fichiers aléatoires, limites de taille / durée / débit, fichiers effacés 60 s après
leur passage, appli jamais exposée directement (seul nginx l'est), service systemd durci, fail2ban.

---

## Ce qu'il faut

| | |
|---|---|
| **Serveur** | Ubuntu 22.04 ou plus récent, utilisateur `ubuntu` avec `sudo`, **port TCP 8000 ouvert** |
| **PC de build** | Windows, Python 3.11+ (testé avec 3.13) |

> Sur Oracle Cloud, AWS, etc., ouvrir le port 8000 se fait dans la console web du fournisseur
> (Oracle : *Networking → Virtual Cloud Networks → votre VCN → Security Lists → Add Ingress Rules*,
> source `0.0.0.0/0`, TCP, port 8000). Le pare-feu interne de la machine est réglé par le script.

---

## 1. Installer le serveur

Sur le serveur, en SSH :

```bash
# Code
git clone https://github.com/fafnnir/live_tchat.git ~/livetchat
cd ~/livetchat

# Configuration : IP publique, salons, clé secrète
cp livetchat/deploy/env.example .env
sed -i "s#^LTCHAT_SECRET=.*#LTCHAT_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))')#" .env
nano .env        # remplacer VOTRE_IP_PUBLIQUE, choisir les salons (séparés par des virgules)

# Mot de passe du groupe (au moins 8 caractères)
python3 -m livetchat.server.set_password

# Installation complète : paquets, service, nginx, certificat, pare-feu, fail2ban, swap
sudo bash livetchat/deploy/deploy.sh
```

Le script se termine par `== OK : https://<IP>:8000`. Il peut être relancé sans risque (après une mise
à jour du code par exemple : `git pull` puis `sudo bash livetchat/deploy/deploy.sh`).

Commandes utiles, changement de mot de passe, mise à jour : voir
[`livetchat/deploy/EXPLOITATION.md`](livetchat/deploy/EXPLOITATION.md).

---

## 2. Compiler le client (Windows)

```powershell
git clone https://github.com/fafnnir/live_tchat.git
cd live_tchat
py -m pip install PyQt6 requests websocket-client "Pillow>=11.2" pillow-heif pyinstaller
```

Deux fichiers propres à **votre** serveur, à placer dans `livetchat\client\` (ils ne sont pas versionnés) :

1. **`server.json`** — copier `server.example.json` et mettre l'IP du serveur :
   ```json
   { "api_base": "https://1.2.3.4:8000" }
   ```
2. **`server_cert.pem`** — le certificat généré par le serveur :
   ```powershell
   scp ubuntu@1.2.3.4:/etc/livetchat/tls/cert.pem livetchat\client\server_cert.pem
   ```

Puis :

```powershell
py -m PyInstaller --noconfirm --clean TchatLive.spec
```

→ `dist\TchatLive.exe`. Pour activer la mise à jour automatique, le déposer aussi sur le serveur :

```powershell
scp dist\TchatLive.exe ubuntu@1.2.3.4:/var/lib/livetchat/downloads/TchatLive.exe
```

---

## 3. Utiliser

1. Envoyer `TchatLive.exe` + le mot de passe du groupe à ses amis (Discord, etc.).
   Rien d'autre à installer.
2. Au premier lancement, Windows peut afficher *« Windows a protégé votre ordinateur »* (exe non signé) :
   *Informations complémentaires → Exécuter quand même*.
3. Taper le mot de passe (une seule fois), choisir son pseudo, cliquer sur un salon, envoyer un média.

À chaque nouvelle version (`livetchat/shared/version.py` + rebuild + dépôt de l'exe sur le serveur),
les clients proposent la mise à jour au démarrage.

---

## Réglages

- Salons, adresse publique : fichier `.env` du serveur (`LTCHAT_CHANNELS`, `LTCHAT_PUBLIC_URL`), puis
  `sudo systemctl restart livetchat`.
- Limites (taille max des fichiers, durée max, taille des files, débit…) : `livetchat/server/settings.py`.
  La taille max des uploads est aussi fixée côté nginx (`client_max_body_size` dans
  `livetchat/deploy/nginx-livetchat.conf`).

## Développement local (sans nginx)

```bash
# Serveur, depuis le dossier du dépôt
export LTCHAT_X_ACCEL=0 LTCHAT_MEDIA_DIR=./tmp/media LTCHAT_DOWNLOAD_DIR=./tmp/dl LTCHAT_SECRET=dev
export LTCHAT_PASSWORD_HASH="$(python -c 'from livetchat.server.security import hash_password;print(hash_password("devpass123"))')"
python -m uvicorn livetchat.server.main:app --port 8001

# Client, dans un autre terminal
LTCHAT_API_BASE=http://127.0.0.1:8001 python -m livetchat.client
```
