#!/bin/bash
# One-shot diagnostics for the TownLine Web VM. Called from Cloud Shell as: bash diag.sh
KEY=~/townline-key.pem
HOST=ubuntu@140.238.157.135
for i in $(seq 1 30); do
  if ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 $HOST 'true' 2>/dev/null; then
    echo "SSH connected on attempt $i"
    break
  fi
  echo "Attempt $i refused, waiting 20s..."
  sleep 20
done
ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 $HOST 'echo "=== deploy.log tail ==="; tail -n 25 /tmp/deploy.log 2>/dev/null; echo "=== service ==="; systemctl is-active townline; echo "=== listening on :80 ==="; sudo ss -tlnp | grep ":80" || echo "nothing on :80"; echo "=== journal ==="; sudo journalctl -u townline --no-pager -n 25 2>/dev/null | tail -n 25; echo "=== localhost curl ==="; curl -s -m 10 -o /dev/null -w "http:%{http_code}\n" http://127.0.0.1/ || echo "curl failed"; echo "=== memory ==="; free -m | head -2'
