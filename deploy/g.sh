#!/bin/bash
# Launches the TownLine Web deploy DETACHED on the VM (survives dropped SSH),
# then polls for completion with short SSH sessions.
# Called from Cloud Shell as: bash g.sh
KEY=~/townline-key.pem
HOST=ubuntu@140.238.157.135
URL=https://raw.githubusercontent.com/yuracbt/townline-web/main/deploy/oracle-runcommand.sh
ssh_base() { ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 $HOST "$@"; }

echo "== launching detached deploy =="
LAUNCHED_OK=0
for i in $(seq 1 30); do
  if ssh_base 'sudo nohup bash -c "curl -sSL '"$URL"' | bash" >>/tmp/deploy.log 2>&1 </dev/null & echo LAUNCHED' 2>/dev/null | grep -q LAUNCHED; then
    echo "Deploy launched on attempt $i."
    LAUNCHED_OK=1
    break
  fi
  echo "Attempt $i: SSH refused, waiting 20s..."
  sleep 20
done

if [ "$LAUNCHED_OK" = "0" ]; then
  echo "LAUNCH FAILED: could not get an SSH session."
  exit 1
fi

echo "== polling for completion (up to ~20 min) =="
for i in $(seq 1 20); do
  sleep 60
  out=$(ssh_base 'grep -q "deploy done" /tmp/deploy.log 2>/dev/null && echo DONE || tail -n 3 /tmp/deploy.log' 2>/dev/null)
  echo "--- poll $i ---"
  echo "$out"
  if echo "$out" | grep -q '^DONE$'; then break; fi
done

echo "== final status =="
ssh_base 'systemctl is-active townline 2>/dev/null; curl -s -o /dev/null -w "http:%{http_code}\n" http://127.0.0.1/ 2>/dev/null || echo http:FAIL'
echo "== deploy.log tail =="
ssh_base 'tail -n 15 /tmp/deploy.log 2>/dev/null || echo "(no log yet)"'
