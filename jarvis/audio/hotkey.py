"""Push-to-talk: start listening the moment a key is pressed, without the wake word.

Why read the input device directly
----------------------------------
A bare modifier like Right Alt cannot be registered as a desktop shortcut — GNOME and Electron
both want a modifier *plus* a key — and on Wayland no application can see another window's
keystrokes. The kernel's evdev interface is the one place a single modifier press is visible, and
this account is in the `input` group, so it can be read without privileges.

What this reads
---------------
Only the key code it was told to watch, and only whether it went down. Every other event is
discarded inside the read loop before anything else sees it: no text, no other key, nothing is
stored, buffered, logged or sent anywhere. It is deliberately incapable of recording what you
type — it compares one integer and throws the rest away.

Set JARVIS_PTT_KEY=none to switch it off entirely.
"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# struct input_event on 64-bit Linux: two longs (timeval), then type, code, value.
_EVENT_FORMAT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)

EV_KEY = 0x01
VALUE_PRESS = 1

# The handful worth naming. Anything else can be given as a raw code.
KEY_CODES = {
    "rightalt": 100,
    "right_alt": 100,
    "altgr": 100,
    "leftalt": 56,
    "rightctrl": 97,
    "leftctrl": 29,
    "rightshift": 54,
    "leftshift": 42,
    "rightmeta": 126,
    "leftmeta": 125,
    "capslock": 58,
    "f13": 183,
    "pause": 119,
    "scrolllock": 70,
}


def resolve_key(name: str) -> Optional[int]:
    """Key name or numeric code → evdev code. None means 'disabled'."""
    raw = (name or "").strip().lower().replace(" ", "").replace("-", "")
    if not raw or raw in ("none", "off", "disabled", "0"):
        return None
    if raw in KEY_CODES:
        return KEY_CODES[raw]
    if raw.isdigit():
        return int(raw)
    return None


def keyboard_devices() -> list[Path]:
    """Event devices that look like keyboards, from /proc/bus/input/devices."""
    found: list[Path] = []
    try:
        blocks = Path("/proc/bus/input/devices").read_text().split("\n\n")
    except OSError:
        return found
    for block in blocks:
        if "Handlers=" not in block:
            continue
        handlers = ""
        ev_bits = ""
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                handlers = line
            elif line.startswith("B: EV="):
                ev_bits = line.split("=", 1)[1].strip()
        if "kbd" not in handlers:
            continue
        # EV bitmask must include EV_KEY (bit 1).
        try:
            if not (int(ev_bits, 16) & (1 << EV_KEY)):
                continue
        except ValueError:
            pass
        for token in handlers.split("=", 1)[1].split():
            if token.startswith("event"):
                path = Path("/dev/input") / token
                if path.exists():
                    found.append(path)
    return found


class PushToTalk:
    """Calls `on_press` whenever the watched key goes down. Silent no-op if it cannot read."""

    def __init__(self, key_code: int, on_press: Callable[[], None],
                 debounce_s: float = 0.4) -> None:
        self.key_code = key_code
        self._on_press = on_press
        # One physical press is visible on more than one event device (the keyboard itself and a
        # consolidated one), so every watcher fires and a single press started two turns. Collapse
        # presses that arrive together; this also absorbs key auto-repeat.
        self._debounce_s = debounce_s
        self._last_fire = 0.0
        self._fire_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.watching: list[str] = []

    def _fire(self) -> None:
        now = time.monotonic()
        with self._fire_lock:
            if now - self._last_fire < self._debounce_s:
                return
            self._last_fire = now
        try:
            self._on_press()
        except Exception:  # noqa: BLE001 - a bad callback must not kill the watcher
            pass

    def start(self) -> bool:
        """Begin watching. False when no keyboard could be opened (missing `input` group)."""
        for device in keyboard_devices():
            try:
                handle = open(device, "rb", buffering=0)
            except OSError:
                continue          # not readable by this user; try the next one
            thread = threading.Thread(target=self._watch, args=(handle,), daemon=True)
            thread.start()
            self._threads.append(thread)
            self.watching.append(device.name)
        return bool(self._threads)

    def stop(self) -> None:
        self._stop.set()

    def _watch(self, handle) -> None:
        try:
            while not self._stop.is_set():
                data = handle.read(_EVENT_SIZE)
                if not data or len(data) < _EVENT_SIZE:
                    return
                _sec, _usec, etype, code, value = struct.unpack(_EVENT_FORMAT, data)
                # The only thing this process ever learns about your typing.
                if etype != EV_KEY or code != self.key_code or value != VALUE_PRESS:
                    continue
                self._fire()
        except OSError:
            return
        finally:
            try:
                handle.close()
            except OSError:
                pass


def start(key: str, on_press: Callable[[], None]) -> Optional[PushToTalk]:
    """Convenience: resolve the key, start watching, return the watcher (None if unavailable)."""
    code = resolve_key(key)
    if code is None:
        return None
    ptt = PushToTalk(code, on_press)
    if not ptt.start():
        return None
    return ptt


def diagnose(key: str = "") -> str:
    """Human-readable reason push-to-talk is or is not available."""
    code = resolve_key(key or os.environ.get("JARVIS_PTT_KEY", "rightalt"))
    if code is None:
        return "Push-to-talk is disabled (JARVIS_PTT_KEY=none)."
    devices = keyboard_devices()
    if not devices:
        return "No keyboard input devices found under /dev/input."
    readable = []
    for d in devices:
        try:
            open(d, "rb").close()
            readable.append(d.name)
        except OSError:
            pass
    if not readable:
        return ("Keyboards found but not readable. Add this account to the 'input' group: "
                "sudo usermod -aG input $USER, then log out and back in.")
    return f"Watching key code {code} on {', '.join(readable)}."
