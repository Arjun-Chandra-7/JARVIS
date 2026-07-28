#!/usr/bin/env bash
# ONE command for the JARVIS WebGL HUD in your browser (no Electron/XWayland needed).
# Starts the backend (:8770), waits until the brain is live, then opens the HUD as an app window.
# Ctrl-C stops the backend.  Add `voice` as an argument to also run the wake-word voice loop.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
PORT="${JARVIS_PORT:-8770}"
URL="http://127.0.0.1:${PORT}/"
unset LD_LIBRARY_PATH LD_PRELOAD || true

[ -x "$PY" ] || { echo "No venv at $PY — run:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

# backend
echo "▸ backend (:${PORT})…"
"$PY" -m jarvis --web >/tmp/jarvis-web.log 2>&1 & WEB=$!
VOICE=""
if [ "${1:-}" = "voice" ]; then
  echo "▸ voice (wake word)…"
  "$PY" -m jarvis --voice >/tmp/jarvis-voice.log 2>&1 & VOICE=$!
fi
trap 'kill $WEB $VOICE 2>/dev/null || true' EXIT

# wait for the brain to be live (health.booting == false), fall back to /stats
echo -n "▸ waiting for brain"
ready=0
for i in $(seq 1 40); do
  sleep 1; echo -n "."
  body="$(curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
  case "$body" in *'"booting":false'*) ready=1; break;; esac
done
[ "$ready" = 1 ] && echo " ready." || echo " (backend slow — opening anyway; check /tmp/jarvis-web.log)"

# open the HUD as a dedicated app window if a Chromium-family browser exists, else default browser
open_app() {
  for b in google-chrome chromium chromium-browser brave-browser microsoft-edge; do
    if command -v "$b" >/dev/null 2>&1; then
      "$b" --app="$URL" --new-window >/dev/null 2>&1 & return 0
    fi
  done
  xdg-open "$URL" >/dev/null 2>&1 & return 0
}
open_app
echo "▸ HUD open at ${URL}  ·  Ctrl-C here to stop Jarvis."

# keep the backend in the foreground so Ctrl-C tears everything down
wait $WEB
