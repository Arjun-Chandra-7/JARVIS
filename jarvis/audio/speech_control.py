"""Cross-process "stop talking" signal.

"Stop speaking" and "cancel this task" are different requests and must stay different. Cutting
Jarvis off mid-sentence should not abandon the research job that produced the sentence, and
cancelling a job should not require sitting through the rest of the audio.

The web server receives the request but the voice process owns the audio device, so the signal
crosses a process boundary through a counter on tmpfs. A counter rather than a flag: playback
compares the value it saw when it started against the current one, so a stop requested *before*
this utterance began cannot silence it, and a stale flag can never mute Jarvis permanently.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_PATH: Optional[Path] = None


def path() -> Path:
    global _PATH
    if _PATH is None:
        base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        _PATH = Path(base) / "jarvis-speech-stop"
    return _PATH


def _read() -> int:
    try:
        return int(path().read_text().strip() or "0")
    except Exception:  # noqa: BLE001
        return 0


def request_stop() -> bool:
    """Ask whoever is speaking to stop. Returns True when something was actually speaking."""
    was_speaking = is_speaking()
    try:
        path().write_text(str(_read() + 1))
    except Exception:  # noqa: BLE001
        return False
    return was_speaking


def token() -> int:
    """The current stop counter. Capture this when playback begins."""
    return _read()


def should_stop(since: int) -> bool:
    """True when a stop was requested after `since` was captured."""
    return _read() > since


# --- "is Jarvis speaking right now?" ---------------------------------------
# Published by the voice process so the web server can answer honestly instead of guessing.
def _speaking_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base) / "jarvis-speaking"


def set_speaking(active: bool) -> None:
    try:
        if active:
            _speaking_path().write_text("1")
        else:
            _speaking_path().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def is_speaking() -> bool:
    return _speaking_path().exists()
