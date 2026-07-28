#!/usr/bin/env bash
# Runs Jarvis: the proactive daemon (reminders, briefings, monitors) + the voice loop, together.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"

# Drop snap/conda library pollution so desktop tools (screenshots, apps) don't crash.
unset LD_LIBRARY_PATH LD_PRELOAD || true

# Voice loop only. Reminders + timers run inside voice mode already; the proactive daemon
# (briefings, email/battery monitors, nightly rollup) is a SEPARATE optional service so it
# doesn't compete with voice for the Claude subscription. Enable it with:
#   JARVIS_DAEMON=1 before starting, or run `python -m jarvis --daemon` in its own terminal.
if [ "${JARVIS_DAEMON:-0}" = "1" ]; then
    "$PY" -m jarvis --daemon &
    trap 'kill %1 2>/dev/null || true' EXIT
fi
exec "$PY" -m jarvis --voice
