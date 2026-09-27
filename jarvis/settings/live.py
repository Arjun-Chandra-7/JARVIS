"""The voice process's side of live settings.

A thread re-reads the preferences a few times a second, pushes the voice's settings into the
objects that use them — the speed factor both voices multiply by, the conversation's follow-up
window — and reports back what those objects now hold. That report, not the file, is what the
backend checks before it says a change took.

Readers in any process can call :func:`voice_speed_factor`; outside the voice process (or
before the watcher starts) it reads the file itself, cached for a second.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

VOICE_KEYS = ("voice.speed", "voice.follow_up_s", "notifications.level", "dictation.history")

_state: dict[str, Any] = {"speed": None, "read_at": 0.0, "session": None, "thread": None}
_lock = threading.Lock()


def voice_speed_factor() -> float:
    with _lock:
        cached, read_at = _state["speed"], _state["read_at"]
    if cached is not None and (_state["thread"] is not None or time.monotonic() - read_at < 1.0):
        return cached
    try:
        from .registry import value

        speed = float(value("voice.speed"))
    except Exception:  # noqa: BLE001 — never let a settings fault stop speech
        speed = 1.0
    with _lock:
        _state["speed"], _state["read_at"] = speed, time.monotonic()
    return speed


def _engine() -> str:
    try:
        from ..audio import kokoro_tts

        return "kokoro" if kokoro_tts.available() else "piper"
    except Exception:  # noqa: BLE001
        return "piper"


def apply_once(session: Optional[object] = None) -> dict[str, Any]:
    """Push the current values into the live objects and report them. Returns the report values."""
    from .. import preferences
    from ..flow import history
    from . import runtime
    from .registry import snapshot

    revision = preferences.revision()
    values = snapshot()
    speed = float(values["voice.speed"])
    with _lock:
        _state["speed"], _state["read_at"] = speed, time.monotonic()
    observed: dict[str, Any] = {"engine": _engine()}
    from ..audio import kokoro_tts

    observed["neutral_speed"] = kokoro_tts.effective_speed(kokoro_tts.NEUTRAL)
    window = None
    conversation = getattr(session, "_conversation", None) if session is not None else None
    if conversation is not None:
        conversation.window_s = float(values["voice.follow_up_s"])
        window = conversation.window_s
    observed["window_s"] = window
    reported = {
        # What the objects hold, read back — not the values just written to them.
        "voice.speed": round(observed["neutral_speed"] / kokoro_tts.NEUTRAL.speed, 2),
        "voice.follow_up_s": int(round(window)) if window is not None else values["voice.follow_up_s"],
        "notifications.level": values["notifications.level"],
        "dictation.history": history.enabled(),
    }
    observed["notifications_readouts"] = preferences.notifications_enabled()
    runtime.report("voice", revision, reported, observed)
    return reported


def _watch(interval: float) -> None:
    from .. import preferences
    from .registry import snapshot

    last: tuple = ()
    while True:
        try:
            key = (preferences.revision(), tuple(snapshot()[k] for k in VOICE_KEYS))
            if key != last:     # a new revision, or a temporary value that has just run out
                apply_once(_state["session"])
                last = key
        except Exception:  # noqa: BLE001 — the watcher must outlive a bad read
            pass
        time.sleep(interval)


def attach_voice(session: object, interval: float = 0.25) -> None:
    """Start (once) the watcher for this voice process and point it at the current session."""
    with _lock:
        _state["session"] = session
        start = _state["thread"] is None
        if start:
            thread = threading.Thread(target=_watch, args=(interval,), name="settings-watch", daemon=True)
            _state["thread"] = thread
    try:
        apply_once(session)     # a rebuilt session gets the current values at once
    except Exception:  # noqa: BLE001
        pass
    if start:
        thread.start()
