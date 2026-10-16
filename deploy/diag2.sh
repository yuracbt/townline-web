#!/bin/bash
# Diagnostics on the fresh townline-web-2 VM via Cloud Shell SSH.
# Called from Cloud Shell as: bash diag2.sh
KEY=~/.ssh/ampere-watch
HOST=ubuntu@40.233.115.145
for i in $(seq 1 10); do
  ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 $HOST 'true' 2>/dev/null && { echo "SSH up on try $i"; break; }
  echo "SSH try $i failed, waiting 15s..."
  sleep 15
done
ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 $HOST '
echo "=== cloud-init tail ==="; sudo tail -n 40 /var/log/cloud-init-output.log 2>/dev/null
echo "=== service ==="; systemctl is-active townline
echo "=== listening ==="; ss -tln | grep -w :80 || echo "nothing on :80"
echo "=== app dir ==="; ls /opt/townline-web 2>/dev/null | head -5 || echo "no app dir"
echo "=== journal ==="; sudo journalctl -u townline --no-pager -n 20 2>/dev/null | tail -20
echo "=== localhost ==="; curl -s -m 10 -o /dev/null -w "http:%{http_code}\n" http://127.0.0.1/ || echo "curl failed"
'
