#!/usr/bin/env bash
# Stop the webcam re-enumerating (and renumbering /dev/videoN) when it resumes.
#
# Some integrated cameras — the Bison 5986:217f in this laptop among them — do not
# survive USB autosuspend cleanly: on resume they re-enumerate with a new device
# number, so any open handle dies and the node index shifts. Symptoms are "frame read
# failed", a camera that works once then never again, and /dev/video0 becoming
# /dev/video1. Pinning power/control to "on" for this one device avoids all of it.
set -euo pipefail

RULE=/etc/udev/rules.d/50-jarvis-camera-nosuspend.rules
VENDOR=${1:-5986}
PRODUCT=${2:-217f}

if [ "$(id -u)" -ne 0 ]; then
  echo "needs root:  sudo $0 [vendorId] [productId]" >&2
  exit 1
fi

cat > "$RULE" <<RULE_EOF
# Jarvis: keep the integrated camera powered so it does not re-enumerate on resume.
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="${VENDOR}", ATTR{idProduct}=="${PRODUCT}", TEST=="power/control", ATTR{power/control}="on"
RULE_EOF

udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --action=add

# Apply immediately to the device that is already plugged in.
for dev in /sys/bus/usb/devices/*/; do
  [ -f "$dev/idVendor" ] || continue
  if [ "$(cat "$dev/idVendor")" = "$VENDOR" ] && [ "$(cat "$dev/idProduct")" = "$PRODUCT" ]; then
    echo on > "$dev/power/control" 2>/dev/null || true
    echo "applied to $dev  (power/control = $(cat "$dev/power/control"))"
  fi
done

echo "wrote $RULE"
echo "Camera autosuspend is now disabled; it will survive reboots."
