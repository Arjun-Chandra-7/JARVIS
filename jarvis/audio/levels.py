"""Publish the live microphone / speech level so the HUD can react to real audio.

The overlay cannot open the microphone itself — the voice loop already holds it, and only one
process gets the device. The voice loop and the web server are also separate processes, so the
level has to cross a process boundary ~20 times a second.

The existing HUD event path (`POST /emit`) is a blocking HTTP call with a 2 s timeout, made from
the thread that is reading audio frames. That is fine for "the user said X" a few times a minute
and completely unusable at frame rate — a single stalled request would drop microphone frames,
which means dropped words.

So this writes a few dozen bytes to a file on tmpfs (`$XDG_RUNTIME_DIR`, i.e. RAM) instead. No
syscall to a socket, no waiting on a server that may be down, and nothing to clean up on crash.
The web server reads the same file on request, and the HUD only polls while something is actually
happening — at idle nobody reads or writes anything.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

_PATH: Optional[Path] = None


def path() -> Path:
    global _PATH
    if _PATH is None:
        base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        _PATH = Path(base) / "jarvis-audio.json"
    return _PATH


def publish(level: float, state: str = "", speech: float = 0.0) -> None:
    """Record the current audio level (0..1), what Jarvis is doing, and speech probability.

    Best effort by design: a failed write must never interrupt audio capture.
    """
    try:
        payload = {
            "level": round(max(0.0, min(1.0, float(level))), 3),
            "speech": round(max(0.0, min(1.0, float(speech))), 3),
            "state": state,
            "t": round(time.time(), 3),
        }
        tmp = path().with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path())
    except Exception:  # noqa: BLE001
        pass


def read(max_age_s: float = 1.0) -> dict:
    """Latest published level, or a zeroed idle reading when it is missing or stale.

    Staleness matters: if the voice process dies mid-utterance the last value would otherwise sit
    there forever and the HUD would animate a level that no microphone is producing.
    """
    try:
        data = json.loads(path().read_text())
        if time.time() - float(data.get("t", 0)) > max_age_s:
            return {"level": 0.0, "speech": 0.0, "state": "", "stale": True}
        data["stale"] = False
        return data
    except Exception:  # noqa: BLE001
        return {"level": 0.0, "speech": 0.0, "state": "", "stale": True}


def clear() -> None:
    try:
        path().unlink()
    except OSError:
        pass
