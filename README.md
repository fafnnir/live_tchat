# LiveTchat

Partage de médias **en direct** entre amis, façon alertes de stream : quelqu'un envoie une image,
un GIF, une vidéo ou un son, et ça s'affiche en même temps chez tous ceux qui sont dans le même
salon — par-dessus le jeu en cours si on veut.

- Salons avec la liste des membres connectés (comme Discord)
- File d'attente par salon : les médias passent un par un
- **Stop** pour arrêter chez soi, **Passer pour tous** pour l'expéditeur
- **Raccourcis clavier globaux** (façon bind Discord) pour *Stop* et *Passer pour tous* : marchent même en jeu,
  combinaison au choix de chacun
- Case *Afficher les médias par-dessus tout* (sinon fenêtre normale), sans voler le focus du jeu
- Choix de l'**écran** d'affichage (bouton *Identifier* pour savoir lequel est lequel)
- Lecture **en mémoire uniquement** : rien n'est enregistré sur le PC des participants, pas besoin de VLC
- Images : jpg, png, gif, webp (animés compris), bmp, tiff, ico, avif, heic (iPhone)… Les formats
  peu courants et les images de plus de 1920 px ou 2 Mo sont convertis en WebP et réduits sur le PC de
  l'expéditeur ; les autres (et les GIF/WebP animés) partent tels quels
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
session signés, liens de média signés qui expirent après l'affichage, contrôle de la signature des
fichiers (premiers octets : ça écarte les fichiers déguisés, mais le contenu n'est pas réencodé),
noms de fichiers aléatoires, limites de taille / durée / débit (3 médias en attente max par personne),
fichiers effacés 60 s après leur passage, textes des autres membres affichés en texte brut, appli
jamais exposée directement (seul nginx l'est), service systemd isolé (ne voit pas le reste de
`/home`, aucun privilège), fail2ban.

Limites connues : le mot de passe est commun au groupe, donc les pseudos ne sont pas vérifiés (un
membre peut prendre le pseudo d'un autre) ; le jeton de session (30 jours) est stocké en clair dans
`~/.tchat_config.json` et ne se révoque qu'en changeant le mot de passe.

---

## Ce qu'il faut

| | |
|---|---|
| **Serveur** | Ubuntu 22.04 ou plus récent, **utilisateur `ubuntu`** avec `sudo` (les chemins `/home/ubuntu/livetchat` sont écrits en dur dans `deploy.sh`, le service et `set_password.py`), **port TCP 8000 ouvert** |
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
                 # (le serveur refuse de démarrer si LTCHAT_SECRET est resté la valeur d'exemple)

# Mot de passe du groupe : à inventer (au moins 8 caractères), c'est celui que vous donnerez à vos amis
python3 -m livetchat.server.set_password

# Installation complète : paquets, service, nginx, certificat, pare-feu, fail2ban, swap
sudo bash livetchat/deploy/deploy.sh
```

Le script se termine par `== OK : https://<IP>:8000`. Il peut être relancé sans risque, par exemple
après une mise à jour du code : `git pull` puis `sudo bash livetchat/deploy/deploy.sh`. À chaque
exécution, il réinstalle aussi les paquets Python aux versions figées dans
`livetchat/deploy/requirements-server.txt` (pour une mise à jour de sécurité : changer la version
dans ce fichier, puis relancer le script).

Commandes utiles, changement de mot de passe, mise à jour : voir
[`livetchat/deploy/EXPLOITATION.md`](livetchat/deploy/EXPLOITATION.md).

---

## 2. Compiler le client (Windows)

```powershell
git clone https://github.com/fafnnir/live_tchat.git
cd live_tchat
py -m pip install -r requirements-client.txt
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

Puis (un dossier par version : un exe en cours d'utilisation ne peut pas être écrasé) :

```powershell
py -m PyInstaller --noconfirm --clean --distpath dist_0.3.4 TchatLive.spec
```

→ `dist_0.3.4\TchatLive.exe`. Pour activer la mise à jour automatique, le publier sur le serveur
(Git Bash) :

```bash
bash livetchat/deploy/publish_client.sh ubuntu@1.2.3.4
```

Le script dépose l'exe, puis écrit sa version dans `version.txt` à côté : c'est cette version que le
serveur annonce aux clients (elle ne dépend pas du code serveur).

---

## 3. Utiliser

1. Envoyer `TchatLive.exe` + le mot de passe du groupe à ses amis (Discord, etc.).
   Rien d'autre à installer.
2. Au premier lancement, Windows peut afficher *« Windows a protégé votre ordinateur »* (exe non signé) :
   *Informations complémentaires → Exécuter quand même*.
3. Taper le mot de passe (une seule fois), choisir son pseudo, cliquer sur un salon, envoyer un média.

Nouvelle version : monter `VERSION` dans `livetchat/shared/version.py`, recompiler dans
`dist_<version>`, puis `publish_client.sh`. Les clients proposent la mise à jour au démarrage.

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
export LTCHAT_X_ACCEL=0 LTCHAT_MEDIA_DIR=./tmp/media LTCHAT_DOWNLOAD_DIR=./tmp/dl
export LTCHAT_SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
export LTCHAT_PASSWORD_HASH="$(python -c 'from livetchat.server.security import hash_password;print(hash_password("devpass123"))')"
python -m uvicorn livetchat.server.main:app --port 8001

# Client, dans un autre terminal
LTCHAT_API_BASE=http://127.0.0.1:8001 python -m livetchat.client
```
