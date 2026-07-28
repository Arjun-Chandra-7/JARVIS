#!/usr/bin/env bash
# One-time setup so Jarvis can control the mouse/keyboard (ydotool) on Wayland.
# Adds you to the `input` group (for /dev/uinput) and installs a user service for ydotoold.
set -euo pipefail

echo "▸ Adding $USER to the 'input' group (needs sudo)…"
sudo usermod -aG input "$USER"

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/ydotoold.service" <<EOF
[Unit]
Description=ydotoold — input daemon for Jarvis desktop control
After=graphical-session.target

[Service]
ExecStart=/usr/bin/ydotoold --socket-path=%t/.ydotool_socket --socket-own=%U:%G
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable ydotoold.service || true

echo
echo "✅ Almost done. The 'input' group change needs a fresh login to take effect."
echo "   Log out and back in (or reboot), then start it with:"
echo "       systemctl --user start ydotoold"
echo "   Verify:  test -S \$XDG_RUNTIME_DIR/.ydotool_socket && echo 'control ready'"
echo
echo "   After that, Jarvis can move the mouse, click, type, and press keys on command."
