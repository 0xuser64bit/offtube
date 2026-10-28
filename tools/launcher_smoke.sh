#!/usr/bin/env bash
# Start offtube the way users do (./run.sh, which builds its venv on first
# run), wait for /api/health, then stop it with SIGTERM as systemd, launchd
# or docker would, and require a clean exit.
set -euo pipefail
cd "$(dirname "$0")/.."

port="${PORT:-18123}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

PORT="$port" ./run.sh >"$log" 2>&1 &
server=$!
for _ in $(seq 1 180); do
  if curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$server" 2>/dev/null; then
    cat "$log"
    echo "run.sh exited before offtube answered" >&2
    exit 1
  fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null; then
  cat "$log"
  echo "offtube never answered on port $port" >&2
  kill -KILL "$server" 2>/dev/null || true
  exit 1
fi

kill -TERM "$server"
status=0
wait "$server" || status=$?
cat "$log"
if [ "$status" -ne 0 ]; then
  echo "offtube exited with status $status on SIGTERM" >&2
  exit 1
fi
echo "run.sh started offtube and it stopped cleanly on SIGTERM."
