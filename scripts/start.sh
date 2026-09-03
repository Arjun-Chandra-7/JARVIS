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
for arg in "$@"; do
    case "$arg" in
        --headless|--no-gui|--daemon)
            ACTION="headless"
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
if [ "$ACTION" = "stop" ]; then
    echo "Stopping Jarvis…"
    if [ -f /tmp/jarvis-overlay.pid ]; then
        PID="$(cat /tmp/jarvis-overlay.pid 2>/dev/null || true)"
        [ -n "$PID" ] && kill -TERM "$PID" 2>/dev/null || true
    fi
    pkill -f "overlay/node_modules/electron" 2>/dev/null || true
    pkill -f "electron.*overlay" 2>/dev/null || true
    pkill -f "electron.*--no-sandbox" 2>/dev/null || true
    systemctl --user stop jarvis-voice.service 2>/dev/null || true
    systemctl --user stop jarvis-backend.service 2>/dev/null || true
    pkill -f "jarvis.*--voice" 2>/dev/null && echo "▸ Voice assistant stopped" || echo "▸ Voice assistant not running"
    pkill -f "jarvis.*--web" 2>/dev/null && echo "▸ Backend stopped" || echo "▸ Backend not running"
    rm -f /tmp/jarvis-web.pid /tmp/jarvis-voice.pid /tmp/jarvis-overlay.pid
    echo "✅ Jarvis stopped."
    exit 0
fi

# -----------------------------------------------------------------------------
# Status action
# -----------------------------------------------------------------------------
if [ "$ACTION" = "status" ]; then
    echo "=== Jarvis Status ==="
    if systemctl --user is-active --quiet jarvis-whatsapp 2>/dev/null || pgrep -f "wa_service.js" >/dev/null 2>&1; then
        echo "▸ WhatsApp Bridge: RUNNING"
    else
        echo "▸ WhatsApp Bridge: STOPPED"
    fi

    if curl -s --max-time 2 http://127.0.0.1:8770/stats >/dev/null 2>&1; then
        echo "▸ Web Backend (:8770): RUNNING (healthy)"
    elif pgrep -f "jarvis.*--web" >/dev/null 2>&1; then
        echo "▸ Web Backend (:8770): RUNNING (unresponsive/booting)"
    else
        echo "▸ Web Backend (:8770): STOPPED"
    fi

    if pgrep -f "jarvis.*--voice" >/dev/null 2>&1; then
        echo "▸ Voice Assistant: RUNNING"
    else
        echo "▸ Voice Assistant: STOPPED"
    fi

    if pgrep -f "electron.*overlay" >/dev/null 2>&1; then
        echo "▸ Electron Overlay: RUNNING"
    else
        echo "▸ Electron Overlay: NOT RUNNING"
    fi
    exit 0
fi

# -----------------------------------------------------------------------------
# 1) WhatsApp bridge
# -----------------------------------------------------------------------------
if systemctl --user is-active --quiet jarvis-whatsapp 2>/dev/null; then
    echo "▸ WhatsApp bridge: already running (systemd)"
elif pgrep -f "wa_service.js" >/dev/null 2>&1; then
    echo "▸ WhatsApp bridge: already running (process)"
else
    if systemctl --user list-unit-files jarvis-whatsapp.service >/dev/null 2>&1 \
       && systemctl --user cat jarvis-whatsapp.service >/dev/null 2>&1; then
        systemctl --user start jarvis-whatsapp 2>/dev/null || true
        echo "▸ WhatsApp bridge: service started"
    elif [ -d "$REPO/whatsapp" ] && command -v node >/dev/null 2>&1; then
        (cd "$REPO/whatsapp" && nohup node wa_service.js >> /tmp/jarvis-whatsapp.log 2>&1 &)
        echo "▸ WhatsApp bridge: started in background"
    else
        echo "▸ WhatsApp bridge: not installed as a service"
    fi
fi

# -----------------------------------------------------------------------------
# 2) Backend (:8770)
# -----------------------------------------------------------------------------
WEB_READY=0
if curl -s --max-time 2 http://127.0.0.1:8770/stats >/dev/null 2>&1; then
    echo "▸ Backend (:8770): already running and healthy"
    WEB_READY=1
else
    if systemctl --user list-unit-files jarvis-backend.service >/dev/null 2>&1 \
       && systemctl --user cat jarvis-backend.service >/dev/null 2>&1; then
        echo "▸ Backend (:8770): starting via systemd service…"
        systemctl --user start jarvis-backend.service 2>/dev/null || true
    else
        pkill -f "jarvis.*--web" 2>/dev/null || true
        echo "▸ Backend (:8770): starting in background…"
        setsid "$PY" -m jarvis --web </dev/null >> /tmp/jarvis-web.log 2>&1 &
        echo $! > /tmp/jarvis-web.pid
    fi

    echo -n "▸ Waiting for backend"
    for i in $(seq 1 30); do
        sleep 1
        echo -n "."
        if curl -s --max-time 1 http://127.0.0.1:8770/stats >/dev/null 2>&1; then
            WEB_READY=1
            echo " ready."
            break
        fi
    done
    if [ "$WEB_READY" -ne 1 ]; then
        echo " (backend starting in background; check logs or systemctl --user status jarvis-backend)"
    fi
fi

# -----------------------------------------------------------------------------
# 3) Voice assistant
# -----------------------------------------------------------------------------
if pgrep -f "jarvis.*--voice" >/dev/null 2>&1; then
    echo "▸ Voice assistant: already running"
else
    if systemctl --user list-unit-files jarvis-voice.service >/dev/null 2>&1 \
       && systemctl --user cat jarvis-voice.service >/dev/null 2>&1; then
        echo "▸ Voice assistant: starting via systemd service…"
        systemctl --user start jarvis-voice.service 2>/dev/null || true
    else
        echo "▸ Voice assistant: starting in background…"
        setsid "$PY" -m jarvis --voice </dev/null >> /tmp/jarvis-voice.log 2>&1 &
        echo $! > /tmp/jarvis-voice.pid
    fi
    echo "▸ Voice assistant: started (listening for wake word)"
fi

# Hotkey binding for overlay toggle (idempotent)
bash "$REPO/scripts/set-hotkey.sh" >/dev/null 2>&1 || true

# -----------------------------------------------------------------------------
# 4) Electron GUI Overlay or Headless completion
# -----------------------------------------------------------------------------
if [ "$ACTION" = "gui" ]; then
    if pgrep -f "overlay/node_modules/electron" >/dev/null 2>&1 || pgrep -f "electron.*overlay" >/dev/null 2>&1; then
        echo "▸ Electron GUI Overlay: already running"
    else
        echo "▸ Electron GUI Overlay: starting detached (no terminal needed)…"
        cd "$REPO/overlay"
        [ -d node_modules ] || { echo "  (installing Electron dependencies…)"; npm install; }
        export JARVIS_OVERLAY_SPAWN=0
        export ELECTRON_OZONE_PLATFORM_HINT=x11
        export ELECTRON_DISABLE_SECURITY_WARNINGS=1
        if [ "$FOREGROUND" = "1" ]; then
            exec node "$REPO/overlay/node_modules/electron/cli.js" "$REPO/overlay" --no-sandbox
        else
            setsid node "$REPO/overlay/node_modules/electron/cli.js" "$REPO/overlay" --no-sandbox </dev/null >> /tmp/jarvis-overlay.log 2>&1 &
            echo $! > /tmp/jarvis-overlay.pid
            echo "▸ Electron GUI Overlay: active"
        fi
    fi
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
