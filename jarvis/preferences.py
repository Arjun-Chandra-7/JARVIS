"""Small, private, cross-process preferences shared by voice and web workers.

Categories are deliberately separate so "mute notifications" only silences passive
readouts. It never silences Jarvis's direct answer to a command, and — unless asked
explicitly — it does not silence job-completion or critical alerts.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_DEFAULTS: dict[str, bool] = {
    "notifications": True,   # passive readouts: incoming messages/calls, monitor alerts
    "job_alerts": True,      # spoken coding-job / background-task completion alerts
}


def state_dir() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()


def _path() -> Path:
    return state_dir() / "preferences.json"


def load() -> dict[str, bool]:
    try:
        data = json.loads(_path().read_text())
        if isinstance(data, dict):
            return {**_DEFAULTS, **{k: bool(v) for k, v in data.items()}}
    except (OSError, ValueError):
        pass
    return dict(_DEFAULTS)


def _write(data: dict[str, bool]) -> None:
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=folder, prefix=".preferences-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(name, 0o600)
        os.replace(name, _path())
    finally:
        if os.path.exists(name):
            os.unlink(name)


def get(key: str) -> bool:
    return bool(load().get(key, _DEFAULTS.get(key, True)))


def set_pref(key: str, value: bool) -> None:
    """Merge one flag into the existing file so unrelated preferences are preserved."""
    data = load()
    data[key] = bool(value)
    _write(data)


def notifications_enabled() -> bool:
    return get("notifications")


def set_notifications(enabled: bool) -> None:
    set_pref("notifications", enabled)


def job_alerts_enabled() -> bool:
    return get("job_alerts")


def set_job_alerts(enabled: bool) -> None:
    set_pref("job_alerts", enabled)
