#!/usr/bin/env bash
# =============================================================================
# JARVIS lifecycle — the single implementation of start / stop / restart / status
# =============================================================================
# `jarvis stop` used to leave things running and break others:
#
#   * jarvis-whatsapp and linkedin-copilot were never stopped at all.
#   * The backend was pkill'd while systemd still wanted it, so `Restart=always`
#     fought the kill and the unit ended up in the `failed` state — which then
#     needs `reset-failed` before it will start cleanly again.
#   * `pkill -f "electron.*--no-sandbox"` matches any Electron app on the machine,
#     not just the overlay.
#
# So: ask systemd first (it owns the units and knows about restart policies), only
# then clean up processes that systemd does not manage, and verify the result
# instead of assuming it.
#
# Used by scripts/start.sh, scripts/stop.sh and scripts/status.sh, which is what
# `jarvis start|stop|restart|--status` reach.
# =============================================================================
set -uo pipefail

CTL_REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTL_PY="$CTL_REPO/.venv/bin/python"
CTL_PORT="${JARVIS_WEB_PORT:-8770}"

# Units in stop order: dependants first, so nothing is restarted by something
# that is still running while we are shutting it down.
JARVIS_UNITS=(jarvis-voice.service jarvis-backend.service jarvis-whatsapp.service)
# Started on demand by the LinkedIn integration rather than by `jarvis start`,
# but it belongs to Jarvis, so "stop everything" must include it.
JARVIS_EXTRA_UNITS=(linkedin-copilot.service)

# --- small helpers -----------------------------------------------------------
# `pgrep -f voice` happily matches any shell whose command line merely mentions it —
# including this script's own parent — so a stopped Jarvis can report itself running.
# Match the full interpreter path, and never count our own process tree.
_running() {
    local pattern="$1" pid
    for pid in $(pgrep -f -- "$pattern" 2>/dev/null); do
        [ "$pid" = "$$" ] && continue
        [ "$pid" = "$PPID" ] && continue
        return 0
    done
    return 1
}

# systemd is the authority for units it owns; only fall back to matching processes for things
# started outside it. Matching on this checkout's interpreter path alone reported a running
# service as stopped whenever the service was started from a different checkout.
_voice_running() {
    _unit_active jarvis-voice.service && return 0
    _running "/.venv/bin/python -m jarvis --voice"
}

_web_running() {
    _unit_active jarvis-backend.service && return 0
    _running "/.venv/bin/python -m jarvis --web"
}

_whatsapp_running() {
    _unit_active jarvis-whatsapp.service && return 0
    _running "whatsapp/wa_service.js"
}

_unit_exists() {
    systemctl --user cat "$1" >/dev/null 2>&1
}

_unit_active() {
    systemctl --user is-active --quiet "$1" 2>/dev/null
}

_backend_healthy() {
    curl -s --max-time 2 "http://127.0.0.1:$CTL_PORT/stats" >/dev/null 2>&1
}

_backend_port_busy() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltnH "sport = :$CTL_PORT" 2>/dev/null | grep -q .
    else
        _backend_healthy || _web_running
    fi
}

# Match only *our* Electron. `overlay/node_modules/electron` is specific to Jarvis — VS Code and
# other Electron apps run from their own install paths — and unlike a full repo path it still
# matches an overlay started from a different checkout of Jarvis.
_overlay_pids() {
    pgrep -f "overlay/node_modules/electron" 2>/dev/null
}

_kill_overlay() {
    local pidfile=/tmp/jarvis-overlay.pid
    if [ -f "$pidfile" ]; then
        local pid
        pid="$(cat "$pidfile" 2>/dev/null || true)"
        [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
        rm -f "$pidfile"
    fi
    local pids
    pids="$(_overlay_pids | sort -u)"
    [ -n "$pids" ] && kill -TERM $pids 2>/dev/null || true
    # The overlay spawns a dbus-monitor to notice screen sharing; it outlives an
    # abrupt kill and holds inherited sockets, so clear it explicitly.
    pkill -f "dbus-monitor --session interface='org.freedesktop.portal.ScreenCast'" 2>/dev/null || true
}

# --- stop --------------------------------------------------------------------
jarvis_stop() {
    local include_extra="${1:-yes}"
    echo "Stopping Jarvis…"

    _kill_overlay

    local unit
    for unit in "${JARVIS_UNITS[@]}"; do
        if _unit_exists "$unit"; then
            systemctl --user stop "$unit" 2>/dev/null || true
        fi
    done
    if [ "$include_extra" = "yes" ]; then
        for unit in "${JARVIS_EXTRA_UNITS[@]}"; do
            _unit_exists "$unit" && systemctl --user stop "$unit" 2>/dev/null || true
        done
    fi

    # Anything started outside systemd (the setsid fallback in start.sh).
    pkill -f "$CTL_REPO/.venv/bin/python -m jarvis --web" 2>/dev/null || true
    pkill -f "$CTL_REPO/.venv/bin/python -m jarvis --voice" 2>/dev/null || true
    pkill -f "node .*${CTL_REPO}/whatsapp/wa_service.js" 2>/dev/null || true

    # Give everything a moment, then escalate only for what is still alive.
    sleep 1
    local pids
    pids="$(_overlay_pids | sort -u)"
    [ -n "$pids" ] && kill -KILL $pids 2>/dev/null || true

    # A unit killed out from under systemd lands in `failed`. It can still be started,
    # but it shows as an error in `systemctl status` and in `jarvis --status` until it
    # is reset, which makes a healthy Jarvis look broken. Clear it here.
    for unit in "${JARVIS_UNITS[@]}" "${JARVIS_EXTRA_UNITS[@]}"; do
        if systemctl --user is-failed --quiet "$unit" 2>/dev/null; then
            systemctl --user reset-failed "$unit" 2>/dev/null || true
        fi
    done

    rm -f /tmp/jarvis-web.pid /tmp/jarvis-voice.pid /tmp/jarvis-overlay.pid

    # Report what is actually down, rather than claiming success.
    local still=()
    _backend_healthy && still+=("backend (:$CTL_PORT)")
    _voice_running && still+=("voice")
    _whatsapp_running && still+=("whatsapp bridge")
    [ -n "$(_overlay_pids)" ] && still+=("overlay")

    if [ ${#still[@]} -eq 0 ]; then
        echo "✅ Jarvis stopped."
        return 0
    fi
    echo "⚠  Still running: ${still[*]}"
    echo "   Check: systemctl --user status jarvis-backend jarvis-voice jarvis-whatsapp"
    return 1
}

# --- start -------------------------------------------------------------------
jarvis_start_services() {
    [ -x "$CTL_PY" ] || { echo "❌ No virtualenv at $CTL_PY"; return 1; }

    # Anything `jarvis stop` stops, `jarvis start` must be able to bring back, or a restart
    # quietly leaves part of Jarvis down until the next reboot. Only units the user has enabled
    # are started, so this never resurrects something deliberately switched off.
    local unit
    for unit in "${JARVIS_EXTRA_UNITS[@]}"; do
        if _unit_exists "$unit" && systemctl --user is-enabled --quiet "$unit" 2>/dev/null \
           && ! _unit_active "$unit"; then
            systemctl --user start "$unit" 2>/dev/null \
                && echo "▸ ${unit%.service}: started" \
                || echo "▸ ${unit%.service}: failed to start"
        fi
    done

    # WhatsApp bridge
    if _whatsapp_running; then
        echo "▸ WhatsApp bridge: already running"
    elif _unit_exists jarvis-whatsapp.service; then
        systemctl --user start jarvis-whatsapp.service 2>/dev/null \
            && echo "▸ WhatsApp bridge: started" \
            || echo "▸ WhatsApp bridge: failed to start"
    elif [ -d "$CTL_REPO/whatsapp" ] && command -v node >/dev/null 2>&1; then
        (cd "$CTL_REPO/whatsapp" \
            && nohup node "$CTL_REPO/whatsapp/wa_service.js" \
                >> /tmp/jarvis-whatsapp.log 2>&1 &)
        echo "▸ WhatsApp bridge: started (no service installed)"
    fi

    # Backend
    if _backend_healthy; then
        echo "▸ Backend (:$CTL_PORT): already running"
    else
        if _unit_exists jarvis-backend.service; then
            systemctl --user reset-failed jarvis-backend.service 2>/dev/null || true
            systemctl --user start jarvis-backend.service 2>/dev/null || true
        else
            setsid "$CTL_PY" -m jarvis --web </dev/null >> /tmp/jarvis-web.log 2>&1 &
            echo $! > /tmp/jarvis-web.pid
        fi
        printf "▸ Backend (:%s): waiting" "$CTL_PORT"
        local i
        for i in $(seq 1 30); do
            sleep 1
            printf "."
            if _backend_healthy; then
                echo " ready."
                break
            fi
        done
        _backend_healthy || echo " not responding yet — see /tmp/jarvis-web.log"
    fi

    # Voice
    if _voice_running; then
        echo "▸ Voice: already running"
    elif _unit_exists jarvis-voice.service; then
        systemctl --user reset-failed jarvis-voice.service 2>/dev/null || true
        systemctl --user start jarvis-voice.service 2>/dev/null \
            && echo "▸ Voice: started (listening for the wake word)" \
            || echo "▸ Voice: failed to start"
    else
        setsid "$CTL_PY" -m jarvis --voice </dev/null >> /tmp/jarvis-voice.log 2>&1 &
        echo $! > /tmp/jarvis-voice.pid
        echo "▸ Voice: started (no service installed)"
    fi
}

jarvis_start_overlay() {
    if [ -n "$(_overlay_pids)" ]; then
        echo "▸ Overlay: already running"
        return 0
    fi
    [ -d "$CTL_REPO/overlay/node_modules" ] || {
        echo "▸ Overlay: installing Electron (one time)…"
        (cd "$CTL_REPO/overlay" && npm install >/dev/null 2>&1) || {
            echo "▸ Overlay: npm install failed"; return 1; }
    }
    # The overlay must not spawn its own backend/voice — this script owns them.
    # `setsid --fork`, not plain `setsid`: without the fork setsid execs in place, stays a child
    # of this shell, and `jarvis restart` then blocks forever waiting on the overlay instead of
    # returning. Every stream is redirected too, so nothing keeps the parent's pipes open.
    ( cd "$CTL_REPO/overlay" \
      && JARVIS_OVERLAY_SPAWN=0 \
         ELECTRON_OZONE_PLATFORM_HINT=x11 \
         ELECTRON_DISABLE_SECURITY_WARNINGS=1 \
         setsid --fork node "$CTL_REPO/overlay/node_modules/electron/cli.js" "$CTL_REPO/overlay" \
             --no-sandbox </dev/null >> /tmp/jarvis-overlay.log 2>&1 )
    echo "▸ Overlay: started"
}

# --- restart -----------------------------------------------------------------
jarvis_restart() {
    local with_gui="${1:-gui}"
    if ! jarvis_stop; then
        echo "❌ Restart cancelled because Jarvis did not stop completely."
        return 1
    fi

    # Wait for the port to actually free up. Starting the backend while the old
    # one still holds :8770 is the classic way to get a half-broken restart.
    local i
    for i in $(seq 1 15); do
        _backend_port_busy || break
        sleep 1
    done

    if _backend_port_busy; then
        echo "❌ Restart cancelled because port $CTL_PORT is still in use."
        return 1
    fi

    echo "Restarting Jarvis…"
    jarvis_start_services
    [ "$with_gui" = "gui" ] && jarvis_start_overlay
    echo
    jarvis_status
}

# --- status ------------------------------------------------------------------
_status_line() {
    printf "  %-22s %s\n" "$1" "$2"
}

jarvis_status() {
    echo "=== Jarvis status ==="

    if _whatsapp_running; then
        _status_line "WhatsApp bridge" "running"
    else
        _status_line "WhatsApp bridge" "stopped"
    fi

    if _backend_healthy; then
        _status_line "Backend (:$CTL_PORT)" "running, healthy"
    elif _web_running; then
        _status_line "Backend (:$CTL_PORT)" "running, not answering yet"
    else
        _status_line "Backend (:$CTL_PORT)" "stopped"
    fi

    if _voice_running; then
        _status_line "Voice" "running"
    else
        _status_line "Voice" "stopped"
    fi

    if [ -n "$(_overlay_pids)" ]; then
        _status_line "Overlay" "running"
    else
        _status_line "Overlay" "stopped"
    fi

    # Surface failed units explicitly: a unit in `failed` will not come back on
    # its own, and silence about it is how a stopped Jarvis looks "fine".
    local unit failed=()
    for unit in "${JARVIS_UNITS[@]}" "${JARVIS_EXTRA_UNITS[@]}"; do
        systemctl --user is-failed --quiet "$unit" 2>/dev/null && failed+=("$unit")
    done
    if [ ${#failed[@]} -gt 0 ]; then
        echo
        echo "  ⚠  failed units: ${failed[*]}"
        echo "     journalctl --user -u ${failed[0]} -n 30"
    fi
}
