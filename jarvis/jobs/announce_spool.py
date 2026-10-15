"""Cross-process announcements: things the backend has to say out loud.

Timers and reminders are set by the agent, and the agent lives in the web backend — typed, phone
and voice turns alike (the voice process forwards to /chat/stream). Their announcers were only
ever registered in the voice process, so a timer fired in the backend with nobody to speak it: the
owner got a notify-send popup and silence. Verified live: a one-minute timer produced only the
D-Bus notification.

The backend drops each announcement here as a small file; the voice process watches the folder
and speaks them. Files older than MAX_AGE_S are discarded unread, so a voice process that was down
does not come back and read out a pile of stale alerts.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

MAX_AGE_S = 120.0


def folder() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    d = Path(base) / "jarvis-announce"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def post(text: str, now: Optional[float] = None) -> bool:
    """Queue `text` to be spoken. Written then renamed, so a reader never sees half a file."""
    text = (text or "").strip()
    if not text:
        return False
    stamp = time.time() if now is None else now
    try:
        d = folder()
        tmp = d / f".{uuid.uuid4().hex}.tmp"
        tmp.write_text(text, encoding="utf-8")
        tmp.rename(d / f"{stamp:.3f}-{uuid.uuid4().hex[:8]}.txt")
        return True
    except OSError:
        return False


def take(now: Optional[float] = None) -> list[str]:
    """Everything waiting, oldest first, removed as it is read. Stale entries are dropped."""
    now = time.time() if now is None else now
    out: list[str] = []
    try:
        files = sorted(folder().glob("*.txt"))
    except OSError:
        return out
    for f in files:
        try:
            stamp = float(f.name.split("-", 1)[0])
            text = f.read_text(encoding="utf-8").strip()
            f.unlink()
        except (OSError, ValueError):
            continue
        if text and now - stamp <= MAX_AGE_S:
            out.append(text)
    return out


def watch(speak: Callable[[str], None], stop: Optional[threading.Event] = None,
          every_s: float = 1.0) -> threading.Thread:
    """Speak whatever the backend posts, from a daemon thread, until `stop` is set."""
    stop = stop or threading.Event()

    def loop() -> None:
        take()                                  # whatever piled up while nobody was listening
        while not stop.is_set():
            for text in take():
                try:
                    speak(text)
                except Exception:  # noqa: BLE001 - one bad announcement must not end the watch
                    pass
            stop.wait(every_s)

    t = threading.Thread(target=loop, daemon=True, name="JarvisAnnounceSpool")
    t.start()
    return t
