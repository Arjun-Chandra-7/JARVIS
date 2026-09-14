#!/usr/bin/env bash
# Report what is actually running, including systemd units left in `failed`.
set -uo pipefail
REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=jarvisctl.sh
. "$REPO/scripts/jarvisctl.sh"
jarvis_status
