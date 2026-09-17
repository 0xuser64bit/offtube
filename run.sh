#!/bin/bash
# macOS / Linux launcher — project-local venv, no system pip pollution.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
./.venv/bin/pip install -q -r requirements.txt
exec ./.venv/bin/python app.py
