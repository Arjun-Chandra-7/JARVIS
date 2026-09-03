#!/usr/bin/env bash
# Direct executable script for Jarvis
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO/bin/jarvis" "$@"
