"""Long-lived, compositor-routed pointer input for Jarvis on Wayland.

Run with system Python (for PyGObject) and python-libei on PYTHONPATH.  The
RemoteDesktop portal owns the permission grant; no raw pointer offsets or
mouse-acceleration guesses are involved.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
from pathlib import Path

from libei import ei, portal

_TOKEN = Path(__file__).resolve().parents[1] / ".jarvis" / "remote-desktop-token"
_BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}


def _reply(**data):
    print(json.dumps(data), flush=True)


def _load_token():
    try:
        return _TOKEN.read_text().strip() or None
    except OSError:
        return None


def _save_token(token):
    if not token:
        return
    _TOKEN.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN.write_text(token)
    _TOKEN.chmod(0o600)


def _devices(sender):
    found = {}
    deadline = time.monotonic() + 10
    wanted = {
        "absolute": ei.DeviceCapability.POINTER_ABSOLUTE,
        "button": ei.DeviceCapability.BUTTON,
        "relative": ei.DeviceCapability.POINTER,
        "keyboard": ei.DeviceCapability.KEYBOARD,
    }
    while time.monotonic() < deadline and "absolute" not in found:
        if not select.select([sender.fd], [], [], max(0, deadline - time.monotonic()))[0]:
            break
        sender.dispatch()
        for event in sender.events:
            if event.event_type is ei.EventType.SEAT_ADDED:
                event.seat.bind(tuple(wanted.values()))
            elif event.event_type is ei.EventType.DEVICE_RESUMED:
                caps = event.device.capabilities
                for name, cap in wanted.items():
                    if cap in caps:
                        found[name] = event.device
    return found


def _inside_region(device, x, y):
    return any(rx <= x < rx + width and ry <= y < ry + height
               for region in device.regions
               for (rx, ry), (width, height) in [(region.position, region.dimension)])


def _run():
    if not portal.is_available() or not ei.is_available():
        _reply(ok=False, reason="PyGObject or libei is unavailable")
        return
    token = _load_token()
    try:
        session = portal.RemoteDesktopSession.negotiate(
            devices=portal.DeviceType.POINTER | portal.DeviceType.KEYBOARD,
            persist_mode=portal.PersistMode.UNTIL_REVOKED,
            restore_token=token,
            timeout=30,
        )
    except portal.PortalDeniedError as exc:
        _reply(ok=False, reason=f"Remote Desktop portal was declined: {exc}")
        return
    except Exception as exc:
        # A saved grant can be revoked; retry once as a fresh request.
        if token:
            try:
                _TOKEN.unlink(missing_ok=True)
                session = portal.RemoteDesktopSession.negotiate(
                    devices=portal.DeviceType.POINTER | portal.DeviceType.KEYBOARD,
                    persist_mode=portal.PersistMode.UNTIL_REVOKED,
                    timeout=30,
                )
            except Exception as retry_exc:
                _reply(ok=False, reason=f"Remote Desktop portal unavailable: {retry_exc}")
                return
        else:
            _reply(ok=False, reason=f"Remote Desktop portal unavailable: {exc}")
            return
    try:
        _save_token(session.restore_token)
        sender = ei.Sender.create_for_fd(session.eis_fd, name="Jarvis screen control")
        devices = _devices(sender)
        absolute = devices.get("absolute")
        if absolute is None or not absolute.regions:
            _reply(ok=False, reason="Compositor supplied no absolute pointer region")
            return
        _reply(ok=True, regions=[{"position": r.position, "size": r.dimension}
                                 for r in absolute.regions])
        for line in sys.stdin:
            try:
                command = json.loads(line)
                op = command.get("op")
                if op == "ready":
                    pass
                elif op == "move":
                    x, y = int(command["x"]), int(command["y"])
                    if not _inside_region(absolute, x, y):
                        raise ValueError("point is outside compositor pointer regions")
                    absolute.start_emulating().pointer_motion_absolute(x, y).frame().stop_emulating()
                elif op == "click":
                    button = _BUTTONS[command.get("button", "left")]
                    device = devices.get("button")
                    if device is None:
                        raise ValueError("compositor supplied no button device")
                    count = 2 if command.get("double") else 1
                    for _ in range(count):
                        device.start_emulating()
                        device.button(button, True).frame()
                        device.button(button, False).frame()
                        device.stop_emulating()
                elif op == "move_click":
                    x, y = int(command["x"]), int(command["y"])
                    if not _inside_region(absolute, x, y):
                        raise ValueError("point is outside compositor pointer regions")
                    button = _BUTTONS[command.get("button", "left")]
                    device = devices.get("button")
                    if device is None:
                        raise ValueError("compositor supplied no button device")
                    absolute.start_emulating().pointer_motion_absolute(x, y).frame().stop_emulating()
                    count = 2 if command.get("double") else 1
                    for _ in range(count):
                        device.start_emulating()
                        device.button(button, True).frame()
                        device.button(button, False).frame()
                        device.stop_emulating()
                else:
                    raise ValueError("unsupported input command")
                _reply(ok=True)
            except Exception as exc:
                _reply(ok=False, reason=str(exc))
    finally:
        session.close()


if __name__ == "__main__":
    _run()
