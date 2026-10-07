#!/bin/bash
# TownLine Web deploy for a fresh Oracle Cloud VM — NO-DOMAIN variant.
# Serves directly on port 80 (plain HTTP) for testing. When you have a domain,
# use deploy/oracle-setup.sh instead (Caddy + automatic HTTPS).
#
# Run on the VM as root (or via OCI Console -> Instance -> Run command):
#   curl -sSL https://raw.githubusercontent.com/yuracbt/townline-web/main/deploy/oracle-runcommand.sh | sudo bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

APP_DIR=/opt/townline-web
REPO=https://github.com/yuracbt/townline-web.git

echo "== packages =="
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git curl

echo "== app =="
if [ ! -d "$APP_DIR/.git" ]; then
  git clone -q "$REPO" "$APP_DIR"
else
  git -C "$APP_DIR" pull -q
fi
cd "$APP_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "== secret key =="
if [ ! -f "$APP_DIR/.env" ]; then
  python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" > "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

echo "== service =="
cat > /etc/systemd/system/townline.service <<'EOF'
[Unit]
Description=TownLine Web
After=network.target

[Service]
User=root
WorkingDirectory=/opt/townline-web
EnvironmentFile=/opt/townline-web/.env
ExecStart=/opt/townline-web/.venv/bin/gunicorn --workers 1 --bind 0.0.0.0:80 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now townline

echo "== firewall =="
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -p tcp --dport 80 -j ACCEPT

echo "== check =="
sleep 4
systemctl is-active --quiet townline && echo "service: active" || echo "service: FAILED"
curl -s -o /dev/null -w "local http: %{http_code}\n" http://127.0.0.1/ || true
echo "deploy done"
