"""Lightweight named timers/alarms Jarvis can set by voice ("5 minute timer").

Timers fire out-of-band (a background thread), so they work regardless of what Jarvis is doing.
On fire we always post a desktop notification (notify-send); if an announcer is registered (voice
mode sets it to speak aloud), we also speak the alert.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable, Optional

_announce: Optional[Callable[[str], None]] = None
_timers: dict[int, tuple[str, float, threading.Timer]] = {}
_counter = [0]
_lock = threading.Lock()


def set_announcer(fn: Optional[Callable[[str], None]]) -> None:
    """Register how fired timers are spoken (voice mode). Desktop notification happens regardless."""
    global _announce
    _announce = fn


def _fire(tid: int, label: str) -> None:
    with _lock:
        _timers.pop(tid, None)
    msg = f"Timer finished: {label}" if label else "Timer finished."
    try:
        subprocess.run(["notify-send", "-u", "critical", "Jarvis", msg], timeout=5)
    except Exception:  # noqa: BLE001
        pass
    if _announce is not None:
        try:
            _announce(msg)
        except Exception:  # noqa: BLE001
            pass


def set_timer(seconds: float, label: str = "") -> int:
    """Start a timer for `seconds`. Returns its id."""
    with _lock:
        _counter[0] += 1
        tid = _counter[0]
    t = threading.Timer(max(1.0, float(seconds)), _fire, args=(tid, label))
    t.daemon = True
    t.start()
    with _lock:
        _timers[tid] = (label, time.time() + seconds, t)
    return tid


def list_timers() -> list[tuple[int, str, int]]:
    """Return active timers as (id, label, seconds_remaining)."""
    now = time.time()
    with _lock:
        return [(tid, label, max(0, int(fire - now))) for tid, (label, fire, _) in _timers.items()]


def cancel_timer(tid: int) -> bool:
    with _lock:
        item = _timers.pop(tid, None)
    if item is not None:
        item[2].cancel()
        return True
    return False
