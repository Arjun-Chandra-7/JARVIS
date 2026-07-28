#!/usr/bin/env bash
# Re-link WhatsApp from scratch: clears the corrupted signal session (Bad MAC) and re-pairs.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
AUTH="$HOME/.local/share/jarvis/whatsapp"

echo "▸ Stopping the bridge service and any stray instances…"
systemctl --user stop jarvis-whatsapp 2>/dev/null || true
pkill -f "node.*wa_service.js" 2>/dev/null || true
sleep 1

echo "▸ Clearing the corrupted session keys…"
rm -rf "$AUTH"

echo
echo "▸ Starting the bridge — SCAN THE QR below in your phone:"
echo "     WhatsApp → Settings → Linked devices → Link a device"
echo "  Wait until it prints 'WhatsApp connected.', then press Ctrl-C."
echo "------------------------------------------------------------------"
node "$REPO/whatsapp/wa_service.js" || true

echo
echo "▸ Handing the bridge back to the always-on service…"
systemctl --user start jarvis-whatsapp
sleep 3
curl -s --max-time 3 http://127.0.0.1:8765/status && echo "  ← connected:true means you're done ✅"
