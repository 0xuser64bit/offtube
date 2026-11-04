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
# Install deps only when missing (fast restarts). --upgrade refreshes the
# pinned set and then moves the download engines to their latest releases:
# a site that changed its player often needs a newer engine, not the pin.
if [ "$1" = "--upgrade" ]; then
  ./.venv/bin/pip install -q -r requirements.txt
  ./.venv/bin/pip install -q -U yt-dlp gallery-dl
elif ! ./.venv/bin/python -c "import yt_dlp, gallery_dl" 2>/dev/null; then
  ./.venv/bin/pip install -q -r requirements.txt
fi
exec ./.venv/bin/python app.py
