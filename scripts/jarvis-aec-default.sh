#!/usr/bin/env bash
# Make the echo-cancelled sink the default output (start), or hand the default back to the
# hardware sink (stop). Used by the jarvis-aec user service.
set -u
node_id() {
    # The numeric id of a node by name, from pw-cli's listing.
    pw-cli ls Node 2>/dev/null | awk -v want="node.name = \"$1\"" '
        /^\s*id [0-9]+,/ { id=$2; sub(",", "", id) }
        index($0, want) { print id; exit }'
}
case "${1:-start}" in
    start)
        for _ in $(seq 1 50); do
            id=$(node_id jarvis_aec_sink)
            [ -n "$id" ] && break
            sleep 0.1
        done
        [ -n "${id:-}" ] || { echo "jarvis_aec_sink did not appear" >&2; exit 1; }
        wpctl set-default "$id"
        ;;
    stop)
        hw=$(pw-cli ls Node 2>/dev/null | awk '
            /^\s*id [0-9]+,/ { id=$2; sub(",", "", id) }
            /node.name = "alsa_output/ { print id; exit }')
        [ -n "$hw" ] && wpctl set-default "$hw"
        ;;
esac
exit 0
