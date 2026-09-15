"""Push-to-talk: the right key starts a turn, and nothing else is ever looked at."""
import struct
import threading
import time

import pytest

from jarvis.audio import hotkey

EV_KEY = 0x01
RIGHT_ALT = 100
LETTER_A = 30


def event(etype: int, code: int, value: int) -> bytes:
    return struct.pack(hotkey._EVENT_FORMAT, 0, 0, etype, code, value)


class FakeDevice:
    """Feeds a scripted sequence of input events, then blocks like a real device would."""

    def __init__(self, events: bytes):
        self._data = events
        self._pos = 0
        self.closed = False

    def read(self, size: int) -> bytes:
        if self._pos >= len(self._data):
            return b""            # EOF ends the watch loop
        chunk = self._data[self._pos : self._pos + size]
        self._pos += size
        return chunk

    def close(self):
        self.closed = True


def run_with(events: bytes, key_code: int = RIGHT_ALT) -> int:
    fired = []
    ptt = hotkey.PushToTalk(key_code, lambda: fired.append(1))
    device = FakeDevice(events)
    thread = threading.Thread(target=ptt._watch, args=(device,), daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    return len(fired)


# ------------------------------------------------------------------ key names
@pytest.mark.parametrize("name,code", [
    ("rightalt", 100), ("right_alt", 100), ("Right Alt", 100), ("altgr", 100),
    ("leftctrl", 29), ("capslock", 58), ("183", 183),
])
def test_key_names_resolve(name, code):
    assert hotkey.resolve_key(name) == code


@pytest.mark.parametrize("name", ["none", "off", "disabled", "", "   ", "not-a-key"])
def test_disabled_or_unknown_keys_resolve_to_none(name):
    assert hotkey.resolve_key(name) is None


# ------------------------------------------------------------------ detection
def test_the_watched_key_fires_on_press():
    assert run_with(event(EV_KEY, RIGHT_ALT, 1)) == 1


def test_release_does_not_fire():
    """Only the press starts a turn; the release must not start a second one."""
    assert run_with(event(EV_KEY, RIGHT_ALT, 0)) == 0


def test_autorepeat_does_not_fire():
    """Holding the key repeats at value 2 — that must not start a turn per repeat."""
    assert run_with(event(EV_KEY, RIGHT_ALT, 2) * 5) == 0


def test_other_keys_are_ignored():
    """The whole point: this must be incapable of reacting to what you type."""
    typed = b"".join(event(EV_KEY, LETTER_A, v) for v in (1, 0)) * 10
    assert run_with(typed) == 0


def test_non_key_events_are_ignored():
    # EV_REL / EV_ABS mouse traffic shares the same stream on some devices.
    assert run_with(event(0x02, RIGHT_ALT, 1) * 3) == 0


def test_repeated_presses_each_fire():
    assert run_with(event(EV_KEY, RIGHT_ALT, 1) * 3) == 3


def test_a_different_configured_key_is_honoured():
    assert run_with(event(EV_KEY, LETTER_A, 1), key_code=LETTER_A) == 1
    assert run_with(event(EV_KEY, RIGHT_ALT, 1), key_code=LETTER_A) == 0


def test_device_is_closed_when_the_stream_ends():
    ptt = hotkey.PushToTalk(RIGHT_ALT, lambda: None)
    device = FakeDevice(b"")
    ptt._watch(device)
    assert device.closed


def test_a_failing_callback_does_not_stop_the_watcher():
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("callback exploded")

    ptt = hotkey.PushToTalk(RIGHT_ALT, boom)
    ptt._watch(FakeDevice(event(EV_KEY, RIGHT_ALT, 1) * 3))
    assert len(calls) == 3, "one bad callback stopped push-to-talk entirely"


def test_start_returns_none_when_disabled():
    assert hotkey.start("none", lambda: None) is None


def test_diagnose_explains_when_disabled():
    assert "disabled" in hotkey.diagnose("none").lower()
