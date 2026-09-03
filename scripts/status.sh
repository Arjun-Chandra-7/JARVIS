#!/usr/bin/env bash
# Check status of Jarvis background processes
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec "$REPO/scripts/start.sh" --status
