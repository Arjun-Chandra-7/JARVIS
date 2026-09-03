#!/usr/bin/env bash
set -e

# Wait for desktop session, PipeWire audio, and display to be ready
sleep 2

# Import active Wayland desktop environment into systemd --user so services can capture display
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS XAUTHORITY || true

# Guarantee Sunshine is restarted with active Wayland display session
systemctl --user restart sunshine.service || true

# Start WhatsApp bridge in background if not running
if ! pgrep -f "wa_service.js" > /dev/null; then
    cd /home/xor_sensei/Dev/Jarvis/whatsapp
    nohup /usr/bin/node wa_service.js > /tmp/jarvis-whatsapp.log 2>&1 &
fi

# Start Jarvis with GUI overlay (no terminal or VS Code window opened)
exec /home/xor_sensei/Dev/Jarvis/scripts/start.sh
