#!/bin/bash
# TownLine Web — put HTTPS on the Oracle VM with nginx + Let's Encrypt.
# Run ONCE on the VM as root, AFTER the domain's A records point here:
#     townsline.com      A  40.233.115.145
#     www.townsline.com  A  40.233.115.145
#
# Usage: sudo bash deploy/https-setup.sh [domain] [admin-email]
set -e

DOMAIN="${1:-townsline.com}"
ADMIN_EMAIL="${2:-yuriy.chobotok@gmail.com}"
APP_DIR="/opt/townline-web"

if [ "$(id -u)" != "0" ]; then
  echo "Run as root: sudo bash deploy/https-setup.sh"
  exit 1
fi

echo "== DNS check =="
if ! getent ahosts "$DOMAIN" >/dev/null 2>&1; then
  echo "ERROR: $DOMAIN does not resolve yet. Add the A records at Porkbun"
  echo "and wait a few minutes, then re-run this script."
  exit 1
fi
echo "$DOMAIN resolves to: $(getent ahosts "$DOMAIN" | head -1 | awk '{print $1}')"

echo "== packages =="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx python3-certbot-nginx curl

echo "== gunicorn -> localhost:5000 =="
cat > /etc/systemd/system/townline.service <<EOF
[Unit]
Description=TownLine Web
After=network.target

[Service]
User=root
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/gunicorn --workers 1 --bind 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl restart townline

echo "== nginx site =="
cat > /etc/nginx/sites-available/townline <<EOF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/townline /etc/nginx/sites-enabled/townline
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

echo "== firewall =="
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -p tcp --dport 443 -j ACCEPT
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -p tcp --dport 80 -j ACCEPT

echo "== certificate =="
certbot --nginx --non-interactive --agree-tos --email "$ADMIN_EMAIL" \
  --redirect -d "$DOMAIN" -d "www.$DOMAIN"

echo "== verify =="
sleep 2
systemctl is-active --quiet townline && echo "townline: active" || echo "townline: FAILED"
systemctl is-active --quiet nginx && echo "nginx: active" || echo "nginx: FAILED"
curl -s -o /dev/null -w "https://$DOMAIN/ -> %{http_code}\n" --max-time 15 "https://$DOMAIN/"
echo "Done. Renewals are automatic via the certbot systemd timer."
