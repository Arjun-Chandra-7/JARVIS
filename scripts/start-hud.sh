#!/usr/bin/env bash
# One command: backend (:8770) + voice + the overlay HUD. Ctrl-C stops everything.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
unset LD_LIBRARY_PATH LD_PRELOAD || true

if [ ! -x "$PY" ]; then
  echo "No venv at $PY — create it and install requirements first."
  exit 1
fi

echo "▸ backend (:8770)…"
"$PY" -m jarvis --web  >/tmp/jarvis-web.log   2>&1 &
WEB=$!
echo "▸ voice…"
"$PY" -m jarvis --voice >/tmp/jarvis-voice.log 2>&1 &
VOICE=$!
trap 'kill $WEB $VOICE 2>/dev/null || true' EXIT

echo -n "▸ waiting for backend"
for i in $(seq 1 30); do
  sleep 1; echo -n "."
  curl -s --max-time 1 http://127.0.0.1:8770/stats >/dev/null 2>&1 && break
done
echo " up."

echo "▸ overlay — Ctrl+Super to toggle. Ctrl-C in this terminal stops all of Jarvis."
cd "$REPO/overlay"
[ -d node_modules ] || { echo "  (first run: installing Electron…)"; npm install; }
export ELECTRON_OZONE_PLATFORM_HINT=x11 ELECTRON_DISABLE_SECURITY_WARNINGS=1
npx electron .
