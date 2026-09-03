#!/usr/bin/env bash
# =============================================================================
# JARVIS — Linux Desktop Application Installer
# =============================================================================
# Installs JARVIS as a first-class Linux Desktop Application:
# - Registers ~/.local/bin/jarvis command in PATH
# - Installs high-res Arc Reactor HUD icons to standard XDG icon directories
# - Registers ~/.local/share/applications/jarvis.desktop (App menu, Dock, Dash)
# - Creates an executable desktop shortcut on ~/Desktop (if available)
# - Supports --uninstall to cleanly remove all desktop integration
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_SRC="$REPO/bin/jarvis"
LOCAL_BIN="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_BASE="$HOME/.local/share/icons/hicolor"
DESKTOP_DIR="$HOME/Desktop"

# -----------------------------------------------------------------------------
# Uninstall Mode
# -----------------------------------------------------------------------------
if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "-u" ]; then
    echo "Removing JARVIS desktop integration…"
    rm -f "$LOCAL_BIN/jarvis"
    rm -f "$APP_DIR/jarvis.desktop"
    rm -f "$DESKTOP_DIR/JARVIS.desktop"
    rm -f "$ICON_BASE/scalable/apps/jarvis.svg"
    rm -f "$ICON_BASE/512x512/apps/jarvis.png"
    rm -f "$ICON_BASE/256x256/apps/jarvis.png"
    rm -f "$ICON_BASE/128x128/apps/jarvis.png"
    rm -f "$ICON_BASE/64x64/apps/jarvis.png"
    rm -f "$ICON_BASE/48x48/apps/jarvis.png"
    
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APP_DIR" 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$ICON_BASE" 2>/dev/null || true
    fi
    echo "✅ JARVIS desktop application has been uninstalled."
    exit 0
fi

echo "=================================================="
echo "  Installing JARVIS as a Linux Desktop Application"
echo "=================================================="

# Ensure generator has run and icons are present
if [ ! -f "$REPO/assets/icons/jarvis.svg" ] || [ ! -f "$REPO/assets/icons/jarvis-512.png" ]; then
    echo "▸ Generating application icons…"
    "$REPO/.venv/bin/python" "$REPO/scripts/generate_icons.py"
fi

# 1. Install CLI launcher into ~/.local/bin
echo "▸ Installing launcher command to $LOCAL_BIN/jarvis…"
mkdir -p "$LOCAL_BIN"
chmod +x "$BIN_SRC"
chmod +x "$REPO/jarvis.sh"
chmod +x "$REPO/scripts/start.sh"
chmod +x "$REPO/scripts/hud.sh"
chmod +x "$REPO/scripts/overlay.sh"
chmod +x "$REPO/scripts/stop.sh"
chmod +x "$REPO/scripts/status.sh"

ln -sf "$BIN_SRC" "$LOCAL_BIN/jarvis"

# 2. Install icons to XDG theme paths
echo "▸ Installing application icons…"
mkdir -p "$ICON_BASE/scalable/apps"
mkdir -p "$ICON_BASE/512x512/apps"
mkdir -p "$ICON_BASE/256x256/apps"
mkdir -p "$ICON_BASE/128x128/apps"
mkdir -p "$ICON_BASE/64x64/apps"
mkdir -p "$ICON_BASE/48x48/apps"

cp -f "$REPO/assets/icons/jarvis.svg" "$ICON_BASE/scalable/apps/jarvis.svg"
cp -f "$REPO/assets/icons/jarvis-512.png" "$ICON_BASE/512x512/apps/jarvis.png"
cp -f "$REPO/assets/icons/jarvis-256.png" "$ICON_BASE/256x256/apps/jarvis.png"
cp -f "$REPO/assets/icons/jarvis-128.png" "$ICON_BASE/128x128/apps/jarvis.png"
cp -f "$REPO/assets/icons/jarvis-64.png" "$ICON_BASE/64x64/apps/jarvis.png"
cp -f "$REPO/assets/icons/jarvis-48.png" "$ICON_BASE/48x48/apps/jarvis.png"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICON_BASE" 2>/dev/null || true
fi

# 3. Generate and install .desktop entry
echo "▸ Installing desktop entry to $APP_DIR/jarvis.desktop…"
mkdir -p "$APP_DIR"

CAT_ICON="$ICON_BASE/512x512/apps/jarvis.png"

cat > "$APP_DIR/jarvis.desktop" <<EOF
[Desktop Entry]
Version=1.5
Type=Application
Name=JARVIS
GenericName=AI Assistant
Comment=Voice-first AI assistant with Iron Man HUD, memory and computer control
Icon=$CAT_ICON
Exec=$LOCAL_BIN/jarvis --gui %u
Terminal=false
Categories=Utility;
Keywords=jarvis;ai;assistant;voice;hud;chat;claude;gemini;ironman;
StartupNotify=true
StartupWMClass=jarvis-overlay
Actions=GUI;Headless;WebHUD;Voice;Text;Status;Stop;

[Desktop Action GUI]
Name=Launch JARVIS (HUD Overlay)
Exec=$LOCAL_BIN/jarvis --gui
Icon=$CAT_ICON

[Desktop Action Headless]
Name=Launch JARVIS (Headless Background)
Exec=$LOCAL_BIN/jarvis --headless
Icon=$CAT_ICON

[Desktop Action WebHUD]
Name=Open WebGL HUD (Browser)
Exec=$LOCAL_BIN/jarvis --hud
Icon=$CAT_ICON

[Desktop Action Voice]
Name=Voice Assistant Mode
Exec=gnome-terminal -- $LOCAL_BIN/jarvis --voice
Icon=$CAT_ICON

[Desktop Action Text]
Name=Terminal Chat Mode
Exec=gnome-terminal -- $LOCAL_BIN/jarvis --text
Icon=$CAT_ICON

[Desktop Action Status]
Name=Check System Status
Exec=gnome-terminal -- $LOCAL_BIN/jarvis --status
Icon=$CAT_ICON

[Desktop Action Stop]
Name=Stop All JARVIS Services
Exec=$LOCAL_BIN/jarvis --stop
Icon=$CAT_ICON
EOF

chmod +x "$APP_DIR/jarvis.desktop"

# Validate
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$APP_DIR/jarvis.desktop" || true
fi

# Update desktop database
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

# 4. Optional Desktop shortcut
if [ -d "$DESKTOP_DIR" ]; then
    echo "▸ Installing shortcut to $DESKTOP_DIR/JARVIS.desktop…"
    cp -f "$APP_DIR/jarvis.desktop" "$DESKTOP_DIR/JARVIS.desktop"
    chmod +x "$DESKTOP_DIR/JARVIS.desktop"
    if command -v gio >/dev/null 2>&1; then
        gio set "$DESKTOP_DIR/JARVIS.desktop" metadata::trusted true 2>/dev/null || true
    fi
fi

echo
echo "=================================================="
echo "✅ JARVIS Application successfully installed!"
echo "=================================================="
echo "• Desktop App: Available in your Ubuntu Applications menu / Dash"
echo "• Command:     Run 'jarvis' anywhere in terminal"
echo "• Actions:     Right-click app icon for GUI, Headless, WebHUD, Voice, Stop"
if [ -d "$DESKTOP_DIR" ]; then
echo "• Shortcut:    Added to your Desktop ($DESKTOP_DIR/JARVIS.desktop)"
fi
echo "• Uninstall:   Run ./scripts/install-app.sh --uninstall"
echo "=================================================="
