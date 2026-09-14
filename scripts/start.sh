#!/usr/bin/env bash
# Start Jarvis services: WhatsApp bridge + backend (:8770) + voice assistant.
# By default or with --headless, runs in the background without popping up any GUI window.
# Use --gui or --overlay to also launch the Electron overlay HUD.
# Use --status to check running state, or --stop to stop Jarvis.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"

# Drop snap/conda library pollution so desktop tools and audio don't fail
unset LD_LIBRARY_PATH LD_PRELOAD || true

# Ensure runtime directory and display environment are set for user session
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DISPLAY="${DISPLAY:-:0}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

if [ -n "${WAYLAND_DISPLAY:-}" ] || [ -n "${DISPLAY:-}" ]; then
    systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS XAUTHORITY XDG_RUNTIME_DIR 2>/dev/null || true
fi

[ -x "$PY" ] || { echo "❌ No virtual environment found at $PY"; exit 1; }

cd "$REPO"

ACTION="gui"
FOREGROUND=0
RESTART_GUI=1
for arg in "$@"; do
    case "$arg" in
        --headless|--no-gui|--daemon)
            ACTION="headless"
            RESTART_GUI=0
            ;;
        --gui|--overlay|--hud)
            ACTION="gui"
            ;;
        -f|--foreground)
            FOREGROUND=1
            ;;
        --stop)
            ACTION="stop"
            ;;
        --restart)
            ACTION="restart"
            ;;
        --status)
            ACTION="status"
            ;;
    esac
done

if [ "${JARVIS_HEADLESS:-0}" = "1" ]; then
    ACTION="headless"
fi

# -----------------------------------------------------------------------------
# Stop action
# -----------------------------------------------------------------------------
. "$REPO/scripts/jarvisctl.sh"

if [ "$ACTION" = "stop" ]; then
    jarvis_stop
    exit $?
fi

if [ "$ACTION" = "restart" ]; then
    jarvis_restart "$([ "$RESTART_GUI" = "1" ] && echo gui || echo headless)"
    exit 0
fi

# -----------------------------------------------------------------------------
# Status action
# -----------------------------------------------------------------------------
if [ "$ACTION" = "status" ]; then
    jarvis_status
    exit 0
fi

# -----------------------------------------------------------------------------
# Services: WhatsApp bridge, backend (:8770), voice.
# One implementation, shared with `--restart` and `jarvis restart`, so starting and
# restarting cannot drift apart. See scripts/jarvisctl.sh.
# -----------------------------------------------------------------------------
jarvis_start_services

# Hotkey binding for overlay toggle (idempotent)
bash "$REPO/scripts/set-hotkey.sh" >/dev/null 2>&1 || true

# -----------------------------------------------------------------------------
# 4) Electron GUI Overlay or Headless completion
# -----------------------------------------------------------------------------
if [ "$ACTION" = "gui" ]; then
    if [ "$FOREGROUND" = "1" ]; then
        # Foreground: hand the terminal to Electron so its output is visible.
        cd "$REPO/overlay"
        [ -d node_modules ] || { echo "  (installing Electron dependencies…)"; npm install; }
        export JARVIS_OVERLAY_SPAWN=0 ELECTRON_OZONE_PLATFORM_HINT=x11 ELECTRON_DISABLE_SECURITY_WARNINGS=1
        exec node "$REPO/overlay/node_modules/electron/cli.js" "$REPO/overlay" --no-sandbox
    fi
    jarvis_start_overlay
    echo
    echo "✅ Jarvis is running with GUI overlay active."
    echo "   • Backend:  http://127.0.0.1:8770"
    echo "   • Voice:    active (say wake word)"
    echo "   • HUD:      Electron overlay active on screen (toggle: Ctrl+Super+Space)"
    echo "   • Status:   ./scripts/status.sh (or ./scripts/start.sh --status)"
    echo "   • Stop:     ./scripts/stop.sh (or ./scripts/start.sh --stop)"
    exit 0
else
    echo
    echo "✅ Jarvis is running headless in the background."
    echo "   • Backend:        http://127.0.0.1:8770"
    echo "   • Voice:          active (say wake word)"
    echo "   • Logs:           /tmp/jarvis-web.log, /tmp/jarvis-voice.log"
    echo "   • Status:         ./scripts/start.sh --status"
    echo "   • Stop:           ./scripts/start.sh --stop (or ./scripts/stop.sh)"
    echo "   • Open HUD later: ./scripts/overlay.sh (Electron) or ./scripts/hud.sh (Browser)"
    exit 0
fi
