#!/bin/bash
# TownLine Web — Mac workflow in one script.
#
#   ./tl.sh run      run the app locally (http://localhost:5000)
#   ./tl.sh update   pull the newest version from GitHub
#   ./tl.sh deploy   deploy GitHub's version to the Oracle VM and restart it
#   ./tl.sh restart  restart the app on the Oracle VM (no code changes)
#   ./tl.sh ssh      open a shell on the Oracle VM
#   ./tl.sh logs     show the app's recent log on the Oracle VM
#
# One-time setup:
#   1. git clone https://github.com/yuracbt/townline-web.git && cd townline-web
#   2. SSH key: Oracle Console -> Cloud Shell, run:  cat ~/.ssh/ampere-watch
#      save that output to ~/.ssh/townline-web-2.key, then:
#        chmod 600 ~/.ssh/townline-web-2.key
set -e
cd "$(dirname "$0")"

VM_HOST="40.233.115.145"          # townline-web-2 (ephemeral IP — update if it ever changes)
VM_USER="ubuntu"
SSH_KEY="$HOME/.ssh/townline-web-2.key"
APP_DIR="/opt/townline-web"

ssh_vm() {
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" "$@"
}

need_key() {
  if [ ! -f "$SSH_KEY" ]; then
    echo "SSH key not found at $SSH_KEY"
    echo "One-time setup: Oracle Console -> Cloud Shell, run: cat ~/.ssh/ampere-watch"
    echo "Save the output to $SSH_KEY, then: chmod 600 $SSH_KEY"
    exit 1
  fi
}

cmd_run() {
  exec bash ./run.sh
}

cmd_update() {
  git pull
  echo "Now at: $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"
}

cmd_deploy() {
  need_key
  echo "Deploying GitHub main to $VM_HOST ..."
  ssh_vm "sudo git -C $APP_DIR pull -q && \
          sudo $APP_DIR/.venv/bin/pip install -q -r $APP_DIR/requirements.txt && \
          sudo systemctl restart townline && \
          sleep 2 && systemctl is-active townline"
  echo "Deployed: http://$VM_HOST/"
  echo "(Note: the VM pulls from GitHub, so 'git push' your changes first if you edited code.)"
}

case "${1:-run}" in
  run) cmd_run ;;
  update) cmd_update ;;
  deploy) cmd_deploy ;;
  restart)
    shift
    need_key
    ssh_vm "sudo systemctl restart townline && sleep 2 && systemctl is-active townline"
    ;;
  ssh) shift; ssh_vm "$@" ;;
  logs) ssh_vm "sudo journalctl -u townline -n 50 --no-pager" ;;
  *) echo "Usage: $0 [run|update|deploy|restart|ssh|logs]"; exit 1 ;;
esac
