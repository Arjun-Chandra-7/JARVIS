#!/usr/bin/env bash
# Wait until the desktop can actually answer a screen-capture request.
#
# Sunshine asks the ScreenCast portal for a display the moment it starts. Started at boot it wins
# that race: the portal is running but not yet able to serve, the request times out, and Sunshine
# carries on with no capture and therefore no working encoder — reporting nvenc, vulkan, vaapi and
# software as all "failed" when the real fault is that there was nothing to encode. It never
# exits, so systemd never restarts it, and remote desktop is simply dead until someone notices.
#
# `After=` only orders the start. This waits for the thing itself.
set -u
deadline=$(( $(date +%s) + ${1:-60} ))

while [ "$(date +%s)" -lt "$deadline" ]; do
    if busctl --user --timeout=3 get-property \
            org.freedesktop.portal.Desktop \
            /org/freedesktop/portal/desktop \
            org.freedesktop.portal.ScreenCast version >/dev/null 2>&1; then
        # Answering at all is the signal; the version it reports does not matter.
        exit 0
    fi
    sleep 2
done

echo "screencast portal did not answer within ${1:-60}s; starting anyway" >&2
exit 0        # never block the service: a late portal is better than no Sunshine
