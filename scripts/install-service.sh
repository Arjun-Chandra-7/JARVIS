#!/usr/bin/env bash
# One-shot: install + start Jarvis as a systemd user service that runs at graphical login.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
chmod +x "$REPO/scripts/jarvis-run.sh"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/jarvis.service" <<EOF
[Unit]
Description=Jarvis — voice assistant + proactive daemon
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$REPO/scripts/jarvis-run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

# Give the service access to the graphical session (audio, screenshots, D-Bus).
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR \
  DBUS_SESSION_BUS_ADDRESS XDG_CURRENT_DESKTOP PATH 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now jarvis.service

echo
echo "✅ Jarvis is running and will start automatically when you log in."
echo "   Live logs:   journalctl --user -u jarvis -f"
echo "   Stop:        systemctl --user stop jarvis"
echo "   Disable:     systemctl --user disable --now jarvis"
