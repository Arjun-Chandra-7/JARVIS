#!/usr/bin/env bash
# Re-link WhatsApp from scratch: clears the corrupted signal session (Bad MAC) and re-pairs.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
AUTH="$HOME/.local/share/jarvis/whatsapp"

echo "▸ Stopping the bridge service and any stray instances…"
systemctl --user stop jarvis-whatsapp 2>/dev/null || true
pkill -f "node.*wa_service.js" 2>/dev/null || true
sleep 1

# The learned address book lives in the same folder as the session keys. Wiping the folder used to
# take contacts.json / contacts-saved.json (2,546 names) with it, so they are set aside first and
# put back after: only the session is corrupt, never the names.
KEEP="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-wa-contacts.XXXXXX")"
for f in contacts.json contacts-saved.json; do
    [ -f "$AUTH/$f" ] && cp -p "$AUTH/$f" "$KEEP/$f"
done
BACKUP="$HOME/.local/share/jarvis/whatsapp-contacts-backup"
mkdir -p "$BACKUP" && cp -p "$KEEP"/*.json "$BACKUP"/ 2>/dev/null || true

echo "▸ Clearing the corrupted session keys (contacts kept)…"
rm -rf "$AUTH"
mkdir -p "$AUTH"
cp -p "$KEEP"/*.json "$AUTH"/ 2>/dev/null || true
rm -rf "$KEEP"

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
