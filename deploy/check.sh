#!/bin/bash
# Post stop/start check: wait for the app, verify it's listening, open port 80 if needed.
# Called from Cloud Shell as: bash check.sh
KEY=~/townline-key.pem
HOST=ubuntu@140.238.157.135
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 $HOST"
for i in $(seq 1 10); do
  $SSH 'true' 2>/dev/null && { echo "SSH up on try $i"; break; }
  echo "SSH try $i failed, waiting 15s..."
  sleep 15
done
$SSH '
for i in $(seq 1 18); do systemctl is-active --quiet townline && break; sleep 10; done
echo "service: $(systemctl is-active townline)"
echo "--- ss :80 ---"
ss -tln | grep -w :80 || echo "nothing on :80"
echo "--- iptables 80 ---"
sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null && echo "ACCEPT present" || { sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT && echo "ACCEPT added"; }
echo "--- localhost ---"
curl -s -m 10 -o /dev/null -w "http:%{http_code}\n" http://127.0.0.1/ || echo "curl failed"
'
