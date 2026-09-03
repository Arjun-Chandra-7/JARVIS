#!/usr/bin/env bash
# =============================================================================
# JARVIS — Standalone Binary Executable Builder
# =============================================================================
# Uses PyInstaller to compile JARVIS into a standalone executable binary.
# - Linux: builds dist/jarvis (ELF binary)
# - Windows (or Wine): builds dist/jarvis.exe
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

PY="$REPO/.venv/bin/python"
PYINSTALLER="$REPO/.venv/bin/pyinstaller"

[ -x "$PY" ] || { echo "❌ No virtual environment found at $PY"; exit 1; }

# Ensure pyinstaller is installed
if [ ! -x "$PYINSTALLER" ]; then
    echo "▸ Installing PyInstaller into virtual environment…"
    "$PY" -m pip install pyinstaller
fi

# Ensure icons are generated
if [ ! -f "$REPO/assets/icons/jarvis.ico" ]; then
    echo "▸ Generating application icons…"
    "$PY" "$REPO/scripts/generate_icons.py"
fi

echo "=================================================="
echo "  Building Standalone JARVIS Executable Binary    "
echo "=================================================="

# Run PyInstaller
"$PYINSTALLER" --clean -y "$REPO/jarvis.spec"

DIST_BIN="$REPO/dist/jarvis"
if [ -f "$DIST_BIN" ]; then
    chmod +x "$DIST_BIN"
    echo
    echo "=================================================="
    echo "✅ Build Successful!"
    echo "=================================================="
    echo "• Standalone binary: $DIST_BIN"
    ls -lh "$DIST_BIN"
    echo
    echo "▸ Running verification test: ./dist/jarvis --help…"
    "$DIST_BIN" --help | head -n 12
    echo "…"
    echo "Verification test passed!"
    echo
    echo "You can run this standalone executable directly:"
    echo "  ./dist/jarvis --status"
    echo "Or copy it anywhere on Linux (e.g. ~/.local/bin/jarvis):"
    echo "  cp dist/jarvis ~/.local/bin/jarvis"
    echo "=================================================="
else
    echo "❌ Build finished but $DIST_BIN was not found."
    exit 1
fi
