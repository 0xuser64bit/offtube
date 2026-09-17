#!/bin/bash
# macOS / Linux launcher — project-local venv, no system pip pollution.
set -e
cd "$(dirname "$0")"
# A venv hardcodes its own path: rebuild it if missing or stale (e.g. the
# project directory was moved or renamed after the venv was created).
if [ ! -x .venv/bin/python ] || [ "$(./.venv/bin/python -c 'import sys; print(sys.prefix)' 2>/dev/null)" != "$PWD/.venv" ]; then
  rm -rf .venv
  python3 -m venv .venv
fi
./.venv/bin/pip install -q -r requirements.txt
exec ./.venv/bin/python app.py
