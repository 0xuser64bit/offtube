#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]]; then
  python="${PYTHON:-.venv/bin/python}"
else
  python="${PYTHON:-python3}"
fi

for dependency in "$python" node ffmpeg ffprobe; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    printf 'Missing validation dependency: %s\n' "$dependency" >&2
    exit 1
  fi
done

"$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required.")'
"$python" -m compileall -q app.py tools
"$python" -m pytest tests -q
for script in web/app.js extension/*.js extension/*/*.js; do
  node --check "$script"
done
node --test tests/extension_gif.test.js tests/test_web_gif.mjs
# The web desk and the extension in Chromium; skipped when Playwright (or,
# for the audits, axe-core; for real input, Xvfb) isn't installed. CI
# installs all three and requires them.
PYTHON="$python" node --test tests/browser/desk.test.mjs tests/browser/extension.test.mjs \
  tests/browser/background.test.mjs
git diff --check
git diff --cached --check
