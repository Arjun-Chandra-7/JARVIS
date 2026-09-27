"""Which commit this process is running.

Read once, at import, because that is the code the process actually loaded: a checkout that is
fast-forwarded later does not change what is already in memory, and "which version is live" is
exactly the question a restart, a repair activation or a rollback has to answer honestly.

The voice service has no HTTP endpoint, so it publishes the same record to a runtime file that
`jarvis status`, the backend and the self-repair health check can all read.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=3, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _read() -> dict:
    commit = _git("rev-parse", "HEAD")
    return {
        "commit": commit[:12] if commit else "unknown",
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        # Tracked files only: an untracked scratch file does not change what was loaded.
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "started_at": time.time(),
        "pid": os.getpid(),
    }


BUILD = _read()


def info() -> dict:
    return dict(BUILD)


def runtime_path(service: str) -> Path:
    base = os.environ.get("JARVIS_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base) / f"jarvis-build-{service}.json"


def publish(service: str) -> None:
    """Record, for other processes, which commit `service` loaded. Never raises."""
    try:
        path = runtime_path(service)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**BUILD, "service": service}))
        os.replace(tmp, path)
    except OSError:
        pass


def published(service: str) -> dict | None:
    """What `service` said it loaded, or None if it has not said (or is not running)."""
    try:
        data = json.loads(runtime_path(service).read_text())
    except (OSError, ValueError):
        return None
    pid = data.get("pid")
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
    return data
