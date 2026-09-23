"""The dictation shortcut, read from the kernel so it works in every window under GNOME Wayland.

Wayland gives no application another window's keystrokes, and GNOME's own shortcuts cannot bind
a bare modifier or report a key's release — which hold-to-talk needs. evdev can (this account is
in the ``input`` group; see audio/hotkey.py for what is and is not read). Only the configured
keys are compared; every other event is discarded unread.

Three ways to use it (JARVIS_DICTATION_MODE):

    hold    press and hold, speak, release to insert
    toggle  press to start, press again to insert
    auto    both: a quick tap starts hands-free dictation (tap again to finish); holding past
            ``tap_s`` is hold-to-talk — the default, because it is what people reach for

Escape (JARVIS_DICTATION_CANCEL_KEY) cancels: nothing is inserted and the clipboard and field
are left alone. Escape still reaches the focused app too — evdev reads, it does not grab.
"""
from __future__ import annotations

import os
import struct
import subprocess
import threading
import time
from typing import Callable, Optional

from ..audio import hotkey

KEY_UP, KEY_DOWN, KEY_REPEAT = 0, 1, 2
ESCAPE = 1


class Gesture:
    """Key events in, start/stop/cancel/confirm out. No I/O, so every rule is tested directly."""

    def __init__(self, mode: str = "auto", tap_s: float = 0.35, clock=time.monotonic,
                 on_start=None, on_stop=None, on_cancel=None, on_confirm=None) -> None:
        self.mode = mode if mode in {"hold", "toggle", "auto"} else "auto"
        self.tap_s, self.clock = tap_s, clock
        self.on_start = on_start or (lambda: None)
        self.on_stop = on_stop or (lambda: None)
        self.on_cancel = on_cancel or (lambda: None)
        self.on_confirm = on_confirm or (lambda: None)
        self.state = "idle"            # idle, held, hands_free, ending, confirming
        self._down_at = 0.0

    def key(self, value: int) -> Optional[str]:
        """The dictation key went down (1), up (0) or repeated (2). Returns what it caused."""
        if value == KEY_REPEAT:
            return None
        now = self.clock()
        if value == KEY_DOWN:
            if self.state == "idle":
                self.state, self._down_at = "held", now
                self.on_start()
                return "start"
            if self.state == "hands_free":
                self.state = "ending"          # the matching release is swallowed
                self.on_stop()
                return "stop"
            if self.state == "confirming":
                self.state = "ending"
                self.on_confirm()
                return "confirm"
            return None
        # released
        if self.state == "held":
            quick = now - self._down_at < self.tap_s
            if self.mode == "toggle" or (self.mode == "auto" and quick):
                self.state = "hands_free"
                return "hands_free"
            self.state = "idle"
            self.on_stop()
            return "stop"
        if self.state == "ending":
            self.state = "idle"
        return None

    def cancel(self) -> Optional[str]:
        if self.state in {"held", "hands_free", "confirming"}:
            self.state = "idle"
            self.on_cancel()
            return "cancel"
        return None

    def await_confirmation(self) -> None:
        """A preview is showing (terminal text): the next press inserts it, Escape drops it."""
        self.state = "confirming"

    def reset(self) -> None:
        self.state = "idle"


class DictationKeys:
    """Watches the dictation key and the cancel key on every keyboard, feeding a Gesture."""

    def __init__(self, key_code: int, gesture: Gesture, cancel_code: Optional[int] = ESCAPE) -> None:
        self.key_code, self.cancel_code, self.gesture = key_code, cancel_code, gesture
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last: tuple[int, int, float] = (-1, -1, 0.0)
        self.watching: list[str] = []

    def _deliver(self, code: int, value: int) -> None:
        # One press shows up on several event devices (the keyboard and a combined one).
        now = time.monotonic()
        with self._lock:
            if (code, value) == self._last[:2] and now - self._last[2] < 0.05:
                return
            self._last = (code, value, now)
            try:
                if code == self.key_code:
                    self.gesture.key(value)
                elif code == self.cancel_code and value == KEY_DOWN:
                    self.gesture.cancel()
            except Exception:  # noqa: BLE001 — a callback failure must not kill the watcher
                pass

    def start(self) -> bool:
        for device in hotkey.keyboard_devices():
            try:
                handle = open(device, "rb", buffering=0)
            except OSError:
                continue
            threading.Thread(target=self._watch, args=(handle,), daemon=True).start()
            self.watching.append(device.name)
        return bool(self.watching)

    def stop(self) -> None:
        self._stop.set()

    def _watch(self, handle) -> None:
        size = struct.calcsize(hotkey._EVENT_FORMAT)
        try:
            while not self._stop.is_set():
                data = handle.read(size)
                if not data or len(data) < size:
                    return
                _s, _u, etype, code, value = struct.unpack(hotkey._EVENT_FORMAT, data)
                # Everything but the two keys we were told to watch is thrown away here.
                if etype != hotkey.EV_KEY or code not in (self.key_code, self.cancel_code):
                    continue
                self._deliver(code, value)
        except OSError:
            return
        finally:
            try:
                handle.close()
            except OSError:
                pass


def conflicts(key: str, ptt_key: str = "", xkb_options: Optional[list[str]] = None) -> list[str]:
    """Why this key may be a bad choice, in words. Empty when it looks fine."""
    problems = []
    code = hotkey.resolve_key(key)
    if code is None:
        return [f"'{key}' is not a key I can watch; use a name like rightalt or a key code."]
    if ptt_key and hotkey.resolve_key(ptt_key) == code:
        problems.append(f"'{key}' is also the push-to-talk key; pick a different one for dictation.")
    if code == ESCAPE:
        problems.append("Escape is the cancel key.")
    if code == 100:
        options = xkb_options if xkb_options is not None else _xkb_options()
        if any(o.startswith(("lv3:ralt", "compose:ralt")) for o in options):
            problems.append("Right Alt is set as AltGr/Compose in your keyboard settings; holding it "
                            "may type special characters. Choose another key or change that option.")
    return problems


def _xkb_options() -> list[str]:
    try:
        raw = subprocess.run(["gsettings", "get", "org.gnome.desktop.input-sources", "xkb-options"],
                             capture_output=True, text=True, timeout=2).stdout
        import re
        return re.findall(r"'([^']+)'", raw)       # "@as []" (none set) gives []
    except (OSError, subprocess.SubprocessError):
        return []


def start(key: str, gesture: Gesture, cancel_key: str = "escape") -> Optional[DictationKeys]:
    code = hotkey.resolve_key(key)
    if code is None:
        return None
    cancel = hotkey.resolve_key(cancel_key) if cancel_key else None
    if cancel is None and (cancel_key or "").lower() in {"esc", "escape"}:
        cancel = ESCAPE
    watcher = DictationKeys(code, gesture, cancel)
    return watcher if watcher.start() else None


def configured() -> tuple[str, str, str]:
    """(key, mode, cancel key) from the environment."""
    return (os.environ.get("JARVIS_DICTATION_KEY", "rightalt"),
            os.environ.get("JARVIS_DICTATION_MODE", "auto").strip().lower(),
            os.environ.get("JARVIS_DICTATION_CANCEL_KEY", "escape"))
