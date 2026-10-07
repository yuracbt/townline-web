#!/bin/bash
# One-shot setup of TownLine Web on a fresh Oracle Cloud Always Free VM
# (Ubuntu 24.04, Ampere ARM). Run as the ubuntu user:
#
#   curl -sSL https://raw.githubusercontent.com/yuracbt/townline-web/main/deploy/oracle-setup.sh \
#     -o oracle-setup.sh && chmod +x oracle-setup.sh
#   ./oracle-setup.sh your-domain.com
#
# Then point your-domain.com's A record at this VM's public IP and open
# https://your-domain.com
set -euo pipefail

DOMAIN="${1:?Usage: $0 your-domain.com}"
APP_DIR=/opt/townline-web
REPO=https://github.com/yuracbt/townline-web.git

echo "== Installing Python, git =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git curl

echo "== Installing Caddy (automatic HTTPS) =="
sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
sudo apt-get update -qq
sudo apt-get install -y -qq caddy

echo "== Fetching TownLine Web =="
if [ ! -d "$APP_DIR/.git" ]; then
  sudo git clone -q "$REPO" "$APP_DIR"
else
  sudo git -C "$APP_DIR" pull -q
fi
sudo chown -R ubuntu:ubuntu "$APP_DIR"
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "== Secret key =="
if [ ! -f "$APP_DIR/.env" ]; then
  python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" \
    > "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

echo "== systemd service =="
sudo tee /etc/systemd/system/townline.service > /dev/null <<EOF
[Unit]
Description=TownLine Web
After=network.target

[Service]
User=ubuntu
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/gunicorn --workers 1 --bind 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now townline

echo "== Opening ports 80/443 on the VM firewall =="
sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null \
  || sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null \
  || sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
# persist across reboots if netfilter-persistent exists
sudo sh -c 'command -v netfilter-persistent >/dev/null && netfilter-persistent save' || true

echo "== Caddy reverse proxy for $DOMAIN =="
sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
$DOMAIN {
	reverse_proxy 127.0.0.1:5000
}
EOF
sudo systemctl reload caddy

echo ""
echo "Done. Final steps:"
echo "  1. In the Oracle console: Networking -> your VCN -> Security Lists ->"
echo "     add Ingress rules for TCP 80 and 443 from 0.0.0.0/0 (if not present)."
echo "  2. Point $DOMAIN's DNS A record at this VM's public IP."
echo "  3. Open https://$DOMAIN and register your account."
