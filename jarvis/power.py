"""Soft on/off ("sleep") state, shared across the voice and web workers.

Sleep does not kill any process — the voice loop keeps listening for the wake word, but
Jarvis stays silent and skips proactive announcements until it's woken again. A hard stop
is `jarvis off` (scripts/stop.sh).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def _state_dir() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()


def _path() -> Path:
    return _state_dir() / "power.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def asleep() -> bool:
    return bool(_read().get("asleep", False))


def since() -> float:
    """Epoch seconds the current sleep began (0 if awake)."""
    d = _read()
    return float(d.get("since", 0)) if d.get("asleep") else 0.0


def set_asleep(value: bool) -> None:
    folder = _state_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"asleep": bool(value), "since": time.time() if value else 0}
    fd, name = tempfile.mkstemp(dir=folder, prefix=".power-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(name, _path())
    finally:
        if os.path.exists(name):
            os.unlink(name)
