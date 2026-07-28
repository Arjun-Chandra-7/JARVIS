#!/usr/bin/env bash
# Launch the JARVIS overlay. Needs the web backend running:  python -m jarvis --web
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO/overlay"

if [ ! -d node_modules ]; then
  echo "▸ First run — installing Electron (~200 MB, one time)…"
  npm install
fi

# Force XWayland so the overlay can be truly always-on-top + transparent on GNOME Wayland.
export ELECTRON_OZONE_PLATFORM_HINT=x11
export ELECTRON_DISABLE_SECURITY_WARNINGS=1
exec npx electron .
