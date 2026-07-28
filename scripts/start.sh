#!/usr/bin/env bash
# ONE command to run all of Jarvis: WhatsApp bridge + backend (:8770) + voice + overlay HUD.
# Ctrl-C stops the backend/voice/overlay; the WhatsApp bridge keeps running (it's a service).
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
unset LD_LIBRARY_PATH LD_PRELOAD || true

[ -x "$PY" ] || { echo "No venv at $PY"; exit 1; }

# 1) WhatsApp bridge (permanent service). Install it once if it isn't set up yet.
if systemctl --user list-unit-files jarvis-whatsapp.service >/dev/null 2>&1 \
   && systemctl --user cat jarvis-whatsapp.service >/dev/null 2>&1; then
  systemctl --user start jarvis-whatsapp 2>/dev/null || true
  echo "▸ WhatsApp bridge: service started"
else
  echo "▸ WhatsApp bridge not installed as a service — run scripts/install-whatsapp-service.sh once."
fi

# 2) backend + 3) voice (background), 4) overlay (foreground)
echo "▸ backend (:8770)…"; "$PY" -m jarvis --web   >/tmp/jarvis-web.log   2>&1 & WEB=$!
echo "▸ voice…";           "$PY" -m jarvis --voice >/tmp/jarvis-voice.log 2>&1 & VOICE=$!
trap 'kill $WEB $VOICE 2>/dev/null || true' EXIT

echo -n "▸ waiting for backend"
for i in $(seq 1 30); do sleep 1; echo -n "."; curl -s --max-time 1 http://127.0.0.1:8770/stats >/dev/null 2>&1 && break; done
echo " ready."

bash "$REPO/scripts/set-hotkey.sh" >/dev/null 2>&1 || true  # ensure Ctrl+Super+Space toggle is bound

echo "▸ overlay — Ctrl+Super+Space to hide/show · Ctrl-C here stops Jarvis."
cd "$REPO/overlay"
[ -d node_modules ] || { echo "  (first run: installing Electron ~200MB…)"; npm install; }
export ELECTRON_OZONE_PLATFORM_HINT=x11 ELECTRON_DISABLE_SECURITY_WARNINGS=1
npx electron .
