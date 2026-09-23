#!/usr/bin/env bash
# Install and start the jarvis-aec user service: WebRTC echo cancellation in front of the
# microphone, with everything played on the machine as the reference signal.
#
#   scripts/install-aec-service.sh            install + start
#   scripts/install-aec-service.sh --remove   stop, uninstall, hand the default output back
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
unit="$HOME/.config/systemd/user/jarvis-aec.service"

if [ "${1:-}" = "--remove" ]; then
    systemctl --user disable --now jarvis-aec.service 2>/dev/null || true
    rm -f "$unit"
    systemctl --user daemon-reload
    "$repo/scripts/jarvis-aec-default.sh" stop
    echo "jarvis-aec removed; the speakers are the default output again."
    exit 0
fi

mkdir -p "$(dirname "$unit")"
cat > "$unit" <<EOF
[Unit]
Description=Jarvis echo cancellation (PipeWire WebRTC AEC)
After=pipewire.service wireplumber.service
Before=jarvis-voice.service
PartOf=default.target

[Service]
Type=simple
ExecStart=/usr/bin/pipewire -c $repo/scripts/pipewire/jarvis-aec.conf
ExecStartPost=$repo/scripts/jarvis-aec-default.sh start
ExecStopPost=$repo/scripts/jarvis-aec-default.sh stop
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now jarvis-aec.service
echo "jarvis-aec running. Restart the voice service to listen on the echo-cancelled microphone:"
echo "    systemctl --user restart jarvis-voice"
