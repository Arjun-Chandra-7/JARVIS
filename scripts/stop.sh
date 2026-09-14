#!/usr/bin/env bash
# Stop everything Jarvis runs. See scripts/jarvisctl.sh for why this is not just pkill.
set -uo pipefail
REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=jarvisctl.sh
. "$REPO/scripts/jarvisctl.sh"
jarvis_stop
