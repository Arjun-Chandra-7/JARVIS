#!/usr/bin/env bash
# Make the WhatsApp bridge a permanent, auto-restarting user service so phone updates never
# silently stop. WhatsApp is already paired (auth is saved), so it reconnects without a QR.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
NODE="$(command -v node || echo /usr/bin/node)"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/jarvis-whatsapp.service" <<EOF
[Unit]
Description=Jarvis WhatsApp bridge (Baileys)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO/whatsapp
ExecStart=$NODE $REPO/whatsapp/wa_service.js
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

# Free the port from any manually-started instance, then hand it to the service.
pkill -f "node.*wa_service.js" 2>/dev/null || true
sleep 1
systemctl --user daemon-reload
systemctl --user enable --now jarvis-whatsapp.service
sleep 2

echo
echo "✅ WhatsApp bridge is now always-on and auto-restarts."
echo "   status:  systemctl --user status jarvis-whatsapp"
echo "   logs/QR: journalctl --user -u jarvis-whatsapp -f"
curl -s --max-time 3 http://127.0.0.1:8765/status && echo "  ← should say connected:true"
