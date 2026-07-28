#!/usr/bin/env bash
# Bind Ctrl+Super to toggle the JARVIS overlay, via a GNOME custom keybinding (reliable on Wayland).
set -euo pipefail

TOGGLE='bash -c "kill -USR2 \$(cat /tmp/jarvis-overlay.pid 2>/dev/null) 2>/dev/null"'
KEY="<Control><Super>space"   # Ctrl+Super+Space — GNOME lets this through; plain Ctrl+Super is often reserved
NAME="Jarvis Overlay Toggle"
PATH_BASE="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
SLOT="$PATH_BASE/jarvis/"

# register the slot in the list (idempotent)
cur=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
if [[ "$cur" != *"$SLOT"* ]]; then
  if [[ "$cur" == "@as []" || "$cur" == "[]" ]]; then new="['$SLOT']"; else new="${cur%]}, '$SLOT']"; fi
  gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$new"
fi

SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$SLOT"
gsettings set "$SCHEMA" name "$NAME"
gsettings set "$SCHEMA" command "$TOGGLE"
gsettings set "$SCHEMA" binding "$KEY"

echo "✅ Bound Ctrl+Super+Space to toggle the overlay."
echo "   (plain Ctrl+Super is reserved by GNOME, so Space is added — change it in Settings ▸ Keyboard if you like.)"
