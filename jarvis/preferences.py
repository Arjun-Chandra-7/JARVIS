"""Small, private, cross-process preferences shared by voice and web workers.

Categories are deliberately separate so "mute notifications" only silences passive
readouts. It never silences Jarvis's direct answer to a command, and — unless asked
explicitly — it does not silence job-completion or critical alerts.

The file also holds the typed settings of ``jarvis.settings`` (animations, voice speed, the
follow-up window, …) under ``"settings"``, a ``"revision"`` that goes up on every change so a
component can say which change it has applied, temporary values with an expiry, and a short
undo history. Every writer goes through :func:`update`, which holds a lock across the
read-modify-write, so the voice and web processes cannot lose each other's changes.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

_DEFAULTS: dict[str, bool] = {
    "notifications": True,   # passive readouts: incoming messages/calls, monitor alerts
    "job_alerts": True,      # spoken coding-job / background-task completion alerts
}

UNDO_DEPTH = 20


def state_dir() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()


def _path() -> Path:
    return state_dir() / "preferences.json"


def _read_raw() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def load_all() -> dict[str, Any]:
    """Everything in the file: the boolean flags, plus settings, temporary values and history."""
    data = _read_raw()
    out: dict[str, Any] = {k: bool(data.get(k, v)) for k, v in _DEFAULTS.items()}
    settings = data.get("settings")
    out["settings"] = dict(settings) if isinstance(settings, dict) else {}
    temporary = data.get("temporary")
    out["temporary"] = dict(temporary) if isinstance(temporary, dict) else {}
    history = data.get("history")
    out["history"] = list(history)[-UNDO_DEPTH:] if isinstance(history, list) else []
    revision = data.get("revision")
    out["revision"] = revision if isinstance(revision, int) and revision >= 0 else 0
    return out


def load() -> dict[str, bool]:
    """The boolean flags only — the shape every older caller expects."""
    data = load_all()
    return {k: data[k] for k in _DEFAULTS}


def revision() -> int:
    return load_all()["revision"]


def _write(data: dict[str, Any]) -> None:
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


@contextmanager
def _locked():
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(folder / ".preferences.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def update(change: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any], Any]:
    """Read, let ``change`` mutate the whole document, bump the revision and write — atomically.

    Returns (the document as written, whatever ``change`` returned). Unknown keys already in the
    file are preserved, so a newer writer never erases what an older one stored.
    """
    with _locked():
        raw = _read_raw()
        data = {**raw, **load_all()}
        result = change(data)
        data["revision"] = int(data.get("revision", 0)) + 1
        data["history"] = list(data.get("history", []))[-UNDO_DEPTH:]
        _write(data)
        return data, result


def get(key: str) -> bool:
    return bool(load().get(key, _DEFAULTS.get(key, True)))


def set_pref(key: str, value: bool) -> None:
    """Merge one flag into the existing file so unrelated preferences are preserved."""
    def change(data: dict[str, Any]) -> None:
        data[key] = bool(value)
    update(change)


def notifications_enabled() -> bool:
    """Passive readouts on? The spoken-notification level decides, including a timed mute."""
    try:
        from .settings import registry

        return registry.value("notifications.level") == "all"
    except Exception:  # noqa: BLE001 — a settings fault must not silence or unsilence anything new
        return get("notifications")


def set_notifications(enabled: bool) -> None:
    from .settings import registry

    # The voice process reads this on every notification, so nothing has to be pushed or awaited.
    registry.set_value("notifications.level", "all" if enabled else "quiet", source="local", verify=False)


def job_alerts_enabled() -> bool:
    try:
        from .settings import registry

        if registry.value("notifications.level") == "urgent":
            return False
    except Exception:  # noqa: BLE001
        pass
    return get("job_alerts")


def set_job_alerts(enabled: bool) -> None:
    set_pref("job_alerts", enabled)
