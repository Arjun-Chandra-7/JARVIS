#!/usr/bin/env bash
# =============================================================================
# JARVIS — re-point installed system integration at this checkout
# =============================================================================
# Moving or renaming the Jarvis folder silently breaks everything that stores an
# absolute path outside the repo: the systemd user units, the `jarvis` command on
# PATH, and the login autostart entry. Run this once after any move:
#
#     bash scripts/relocate.sh                    # point at this checkout
#     bash scripts/relocate.sh /path/to/Jarvis    # point at another checkout
#
# It only rewrites integration that is already installed — it never installs,
# enables, or disables anything. Use scripts/install-app.sh and
# scripts/install-service.sh for first-time setup.
# =============================================================================
set -euo pipefail

REPO="${1:-$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
REPO="$(cd -P "$REPO" && pwd)"
[ -x "$REPO/bin/jarvis" ] || { echo "Not a Jarvis checkout: $REPO" >&2; exit 1; }

UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART="$HOME/.config/autostart/jarvis.desktop"
LOCAL_BIN="$HOME/.local/bin/jarvis"
NODE="$(command -v node || echo /usr/bin/node)"
UID_NUM="$(id -u)"
changed=0

echo "Re-pointing Jarvis integration at: $REPO"
echo

# --- systemd user units -------------------------------------------------------
# Only units that already exist are rewritten, so an install you never made stays
# uninstalled and every unit's enable/disable state is left exactly as it is.
unit_body() {
    case "$1" in
    jarvis-backend.service) cat <<EOF
[Unit]
Description=Jarvis Web Backend (:8770)
After=network.target sound.target
PartOf=default.target

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStart=$REPO/.venv/bin/python -m jarvis --web
Restart=always
RestartSec=3
Environment=HOME=$HOME
Environment=USER=$USER
Environment=XDG_RUNTIME_DIR=/run/user/$UID_NUM

[Install]
WantedBy=default.target
EOF
        ;;
    jarvis-voice.service) cat <<EOF
[Unit]
Description=Jarvis Voice Assistant (Wake word + Speech)
After=jarvis-backend.service sound.target
PartOf=default.target

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStart=$REPO/.venv/bin/python -m jarvis --voice
Restart=always
RestartSec=5
Environment=HOME=$HOME
Environment=USER=$USER
Environment=XDG_RUNTIME_DIR=/run/user/$UID_NUM

[Install]
WantedBy=default.target
EOF
        ;;
    jarvis-whatsapp.service) cat <<EOF
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
        ;;
    jarvis.service) cat <<EOF
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
        ;;
    jarvis-autostart.service) cat <<EOF
[Unit]
Description=Jarvis Keepalive (Runs start.sh)
After=network.target sound.target graphical-session.target

[Service]
Type=oneshot
ExecStart=$REPO/scripts/start.sh
Environment=HOME=$HOME
Environment=USER=$USER
Environment=XDG_RUNTIME_DIR=/run/user/$UID_NUM
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-0
EOF
        ;;
    esac
}

restart_list=()
for unit in jarvis-backend.service jarvis-voice.service jarvis-whatsapp.service \
            jarvis.service jarvis-autostart.service; do
    target="$UNIT_DIR/$unit"
    if [ ! -f "$target" ]; then
        printf '  %-28s not installed — skipped\n' "$unit"
        continue
    fi
    if unit_body "$unit" | cmp -s - "$target"; then
        printf '  %-28s already correct\n' "$unit"
        continue
    fi
    was_active=no
    if systemctl --user is-active --quiet "$unit"; then
        was_active=yes
    fi
    unit_body "$unit" > "$target"
    printf '  %-28s rewritten\n' "$unit"
    changed=1
    if [ "$was_active" = yes ]; then
        restart_list+=("$unit")
    fi
done

# --- `jarvis` on PATH ---------------------------------------------------------
echo
if [ -L "$LOCAL_BIN" ] || [ -e "$LOCAL_BIN" ]; then
    if [ "$(readlink -f "$LOCAL_BIN" || true)" = "$REPO/bin/jarvis" ]; then
        echo "  ~/.local/bin/jarvis          already correct"
    else
        ln -sfn "$REPO/bin/jarvis" "$LOCAL_BIN"
        echo "  ~/.local/bin/jarvis          re-pointed"
        changed=1
    fi
else
    echo "  ~/.local/bin/jarvis          not installed — skipped"
fi

# --- login autostart entry ----------------------------------------------------
if [ -f "$AUTOSTART" ]; then
    if grep -qx "Exec=$REPO/start_jarvis.sh" "$AUTOSTART"; then
        echo "  autostart/jarvis.desktop     already correct"
    else
        sed -i "s|^Exec=.*|Exec=$REPO/start_jarvis.sh|" "$AUTOSTART"
        echo "  autostart/jarvis.desktop     re-pointed"
        changed=1
    fi
else
    echo "  autostart/jarvis.desktop     not installed — skipped"
fi

# --- apply --------------------------------------------------------------------
echo
if [ "$changed" -eq 0 ]; then
    echo "Nothing to change — everything already points here."
    exit 0
fi

systemctl --user daemon-reload

for unit in ${restart_list[@]+"${restart_list[@]}"}; do
    echo "  restarting $unit"
    systemctl --user restart "$unit" \
        || echo "    (restart failed — check: journalctl --user -u $unit -n 30)"
done

echo
echo "✅ Done. Verify with:  bash scripts/status.sh"
