#!/bin/bash
# TownLine Web — local run script (macOS / Linux).
# First run: creates .venv and installs dependencies. Then starts the app.
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Get it at https://www.python.org/downloads/"
  exit 1
fi
if [ ! -d .venv ]; then
  echo "Creating virtual environment (one-time setup)..."
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
# macOS reserves port 5000 for AirPlay Receiver — use the first free port.
if [ -z "${PORT:-}" ]; then
  PORT=5000
  if command -v lsof >/dev/null 2>&1; then
    for p in 5000 5001 5002 5003 5004; do
      if ! lsof -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then PORT=$p; break; fi
    done
  fi
fi
export PORT
echo "Starting TownLine Web on http://localhost:$PORT ..."
echo "(Ctrl+C to stop)"
.venv/bin/python app.py
