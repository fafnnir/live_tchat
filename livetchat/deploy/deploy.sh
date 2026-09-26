#!/usr/bin/env bash
# Installation / mise à jour du serveur LiveTchat (Ubuntu, Oracle Cloud).
# Usage : sudo bash ~/livetchat/livetchat/deploy/deploy.sh
# Idempotent : peut être relancé sans risque. Le fichier ~/livetchat/.env doit exister
# (LTCHAT_SECRET + LTCHAT_PASSWORD_HASH, voir server/set_password.py).
#
# Réseau : seul le port 8000 est ouvert chez Oracle. nginx y fait le HTTPS avec un
# certificat auto-signé (épinglé dans le client), l'appli écoute en local sur 8001.
set -euo pipefail

APP=/home/ubuntu/livetchat
DATA=/var/lib/livetchat
TLS=/etc/livetchat/tls
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -f "$APP/.env" ] || { echo "ERREUR : $APP/.env manquant (voir README)"; exit 1; }
sed -i 's/\r$//' "$APP/.env"
# IP publique : lue dans LTCHAT_PUBLIC_URL=https://<IP>:8000 du .env
PUBLIC_IP="$(sed -n 's#^LTCHAT_PUBLIC_URL=https://\([0-9.]*\).*#\1#p' "$APP/.env")"
[ -n "$PUBLIC_IP" ] || { echo "ERREUR : LTCHAT_PUBLIC_URL=https://<IP>:8000 manquant dans $APP/.env"; exit 1; }
chown ubuntu:ubuntu "$APP/.env"; chmod 600 "$APP/.env"

echo "== 0. Paquets et environnement Python (seulement s'ils manquent)"
for pkg in nginx fail2ban python3-venv iptables-persistent; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="${MISSING:-} $pkg"
done
if [ -n "${MISSING:-}" ]; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $MISSING >/dev/null
fi
if [ ! -x "$APP/venv/bin/uvicorn" ]; then
    sudo -u ubuntu python3 -m venv "$APP/venv"
    sudo -u ubuntu "$APP/venv/bin/pip" install -q fastapi "uvicorn[standard]" python-multipart
fi

echo "== 1. Swap 1 Go (filet de sécurité, la VM n'a que 1 Go de RAM)"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/99-livetchat.conf
sysctl -q -p /etc/sysctl.d/99-livetchat.conf

echo "== 2. Dossiers de données"
install -d -o ubuntu -g www-data -m 2750 "$DATA" "$DATA/media" "$DATA/downloads"

echo "== 3. Certificat TLS auto-signé (10 ans)"
if [ ! -f "$TLS/cert.pem" ]; then
    install -d -m 755 "$TLS"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
        -subj "/CN=LiveTchat" -addext "subjectAltName=IP:$PUBLIC_IP" \
        -keyout "$TLS/key.pem" -out "$TLS/cert.pem" 2>/dev/null
    chmod 600 "$TLS/key.pem"; chmod 644 "$TLS/cert.pem"
fi

echo "== 4. Service systemd (remplace l'ancien lancement nohup)"
pkill -f "uvicorn livetchat.server.main:app --host 0.0.0.0" || true
install -m 644 "$HERE/livetchat.service" /etc/systemd/system/livetchat.service
systemctl daemon-reload
systemctl enable livetchat >/dev/null
systemctl restart livetchat
sleep 2
systemctl is-active --quiet livetchat || { journalctl -u livetchat -n 30 --no-pager; exit 1; }

echo "== 5. nginx (HTTPS sur 8000)"
install -m 644 "$HERE/nginx-livetchat.conf" /etc/nginx/sites-available/livetchat
ln -sf /etc/nginx/sites-available/livetchat /etc/nginx/sites-enabled/livetchat
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl reload nginx

echo "== 6. Pare-feu : trafic local (nginx -> appli) et port 8000 autorisés"
iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -i lo -j ACCEPT
iptables -C INPUT -p tcp -m state --state NEW -m tcp --dport 8000 -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 2 -p tcp -m state --state NEW -m tcp --dport 8000 -j ACCEPT
[ -f /etc/iptables/rules.v4 ] || iptables-save > /etc/iptables/rules.v4
python3 - <<'PY'
p = "/etc/iptables/rules.v4"
lines = [l for l in open(p).read().splitlines() if "f2b-" not in l]   # fail2ban recrée ses règles
wanted = ["-A INPUT -i lo -j ACCEPT", "-A INPUT -p tcp -m state --state NEW -m tcp --dport 8000 -j ACCEPT"]
start = lines.index("*filter")
first = next(i for i, l in enumerate(lines) if i > start and (l.startswith("-A ") or l == "COMMIT"))
for rule in reversed(wanted):
    if rule not in lines:
        lines.insert(first, rule)
open(p, "w").write("\n".join(lines) + "\n")
PY

echo "== 7. fail2ban (lit maintenant le log nginx)"
install -m 644 "$HERE/fail2ban-livetchat.conf" /etc/fail2ban/filter.d/livetchat-nginx.conf
install -m 644 "$HERE/jail-livetchat.local" /etc/fail2ban/jail.d/livetchat.local
systemctl restart fail2ban

echo "== OK : https://$PUBLIC_IP:8000"
