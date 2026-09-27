#!/usr/bin/env bash
# GNOME keybindings for the teaching overlay, alongside the existing "Jarvis Overlay Toggle".
#
#   emergency dismiss   <Control><Super>Escape   → SIGURG to the overlay (hides it at once)
#   pen mode            <Control><Super>p        → "pen" on the overlay's control socket
#
# GNOME on Wayland only lets an X11 key grab see keys while an X11 window has focus, so Electron's
# own shortcuts are not enough; a GNOME keybinding works whatever has focus. Idempotent: running
# it again changes nothing. Override the keys with TEACH_DISMISS_KEY / TEACH_PEN_KEY.
#   Remove: scripts/install-teach-shortcuts.sh --remove
set -euo pipefail

BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
SCHEMA=org.gnome.settings-daemon.plugins.media-keys.custom-keybinding
DISMISS_KEY=${TEACH_DISMISS_KEY:-<Control><Super>Escape}
PEN_KEY=${TEACH_PEN_KEY:-<Control><Super>p}

current=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
[ "$current" = "@as []" ] && current="[]"

add_path() {
  case "$current" in *"'$1'"*) ;; "[]") current="['$1']" ;; *) current="${current%]}, '$1']" ;; esac
}
drop_path() {
  current=$(printf '%s' "$current" | sed -e "s#, '$1'##" -e "s#'$1', ##" -e "s#'$1'##")
}

if [ "${1:-}" = "--remove" ]; then
  drop_path "$BASE/jarvis-teach-dismiss/"
  drop_path "$BASE/jarvis-teach-pen/"
  gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$current"
  echo "removed"
  exit 0
fi

set_binding() {  # path name key command
  gsettings set "$SCHEMA:$1" name "$2"
  gsettings set "$SCHEMA:$1" binding "$3"
  gsettings set "$SCHEMA:$1" command "$4"
}

set_binding "$BASE/jarvis-teach-dismiss/" "Jarvis Teaching Overlay: Emergency Dismiss" "$DISMISS_KEY" \
  'bash -c "kill -URG \$(cat /tmp/jarvis-overlay.pid 2>/dev/null) 2>/dev/null"'
set_binding "$BASE/jarvis-teach-pen/" "Jarvis Teaching Overlay: Pen Mode" "$PEN_KEY" \
  'bash -c "printf pen | nc -U -q1 \${XDG_RUNTIME_DIR:-/run/user/\$(id -u)}/jarvis-teach.sock"'
add_path "$BASE/jarvis-teach-dismiss/"
add_path "$BASE/jarvis-teach-pen/"
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$current"
echo "installed: dismiss=$DISMISS_KEY pen=$PEN_KEY"
