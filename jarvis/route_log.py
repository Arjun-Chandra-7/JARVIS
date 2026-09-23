"""Which way each request went, written down without what it said.

"Why did it answer from memory?" could not be answered from the journal: it showed the words
heard and the words spoken and nothing about the path between them. Each deterministic decision
now leaves one row — intent, where the context came from, reply language, action — in
``route-events.jsonl`` beside the approval audit, and one short line on stdout.

Never a message body, never a whole phone number: digit runs of seven or more are masked before
anything is written, and callers pass lengths and masked labels, not text.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from pathlib import Path

RECENT: deque = deque(maxlen=200)
_LONG_DIGITS = re.compile(r"\+?\d[\d\s-]{5,}\d")


def _mask(m: re.Match) -> str:
    return f"<number …{re.sub(r'[^0-9]', '', m.group(0))[-4:]}>"


def _redact(value):
    if isinstance(value, str):
        return _LONG_DIGITS.sub(_mask, value)[:160]
    return value


def _path() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser() / "route-events.jsonl"


def record(**fields) -> dict:
    from . import context
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": context.current()}
    row.update({k: _redact(v) for k, v in fields.items() if v is not None and v != ""})
    RECENT.append(row)
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass
    summary = " ".join(f"{k}={row[k]}" for k in ("intent", "action", "context", "lang", "memory")
                       if k in row)
    print(f"  route {summary}", flush=True)
    return row
