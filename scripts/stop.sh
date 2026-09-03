#!/usr/bin/env bash
# Stop all Jarvis background processes
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec "$REPO/scripts/start.sh" --stop
