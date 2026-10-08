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
echo "Starting TownLine Web on http://localhost:5000 ..."
echo "(Ctrl+C to stop)"
.venv/bin/python app.py
