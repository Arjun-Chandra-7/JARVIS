"""What was dictated, kept briefly on this machine so it can be pasted again or retried.

``$JARVIS_STATE_DIR/dictation-history.jsonl``, mode 0600, outside the repository. Each row: when,
which app, the profile, the raw transcript, the cleaned text, and what happened to it. Never the
field's contents or anything from a password field, never audio, never the service log.

JARVIS_DICTATION_HISTORY=off keeps nothing. JARVIS_DICTATION_HISTORY_HOURS (default 24) and a
200-row cap bound what is kept; "clear dictation history" empties it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

MAX_ROWS = 200


def enabled() -> bool:
    return os.environ.get("JARVIS_DICTATION_HISTORY", "on").strip().lower() not in {"0", "off", "false", "no"}


def _hours() -> float:
    try:
        return max(0.0, float(os.environ.get("JARVIS_DICTATION_HISTORY_HOURS", "24")))
    except ValueError:
        return 24.0


def _path() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser() / "dictation-history.jsonl"


def _rows() -> list[dict]:
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _write(rows: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def prune(now: Optional[float] = None) -> list[dict]:
    now = now or time.time()
    keep_s = _hours() * 3600
    rows = [r for r in _rows() if now - float(r.get("t", 0)) <= keep_s][-MAX_ROWS:]
    if _path().exists():
        _write(rows)
    return rows


def add(*, app: str, profile: str, raw: str, cleaned: str, status: str, reason: str = "",
        method: str = "") -> Optional[str]:
    if not enabled():
        return None
    row = {"id": uuid.uuid4().hex[:10], "t": time.time(), "app": app[:60], "profile": profile,
           "raw": raw, "cleaned": cleaned, "status": status, "reason": reason[:200], "method": method}
    rows = prune() + [row]
    _write(rows[-MAX_ROWS:])
    return row["id"]


def update(row_id: str, **fields) -> None:
    rows = _rows()
    for r in rows:
        if r.get("id") == row_id:
            r.update(fields)
    if rows:
        _write(rows)


def last(with_text: bool = True) -> Optional[dict]:
    for r in reversed(prune()):
        if not with_text or r.get("cleaned"):
            return r
    return None


def discard_last() -> bool:
    """Forget the most recent dictation (it stays wherever it was typed)."""
    rows = prune()
    if not rows:
        return False
    _write(rows[:-1])
    return True


def clear() -> int:
    n = len(_rows())
    try:
        _path().unlink()
    except OSError:
        pass
    return n
