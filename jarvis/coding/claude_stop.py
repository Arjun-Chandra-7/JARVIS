"""Claude Stop hook events, shared with the voice process without screen or clipboard access."""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

from ..preferences import state_dir


def _events_file() -> Path:
    return state_dir() / "claude-stop-events.jsonl"


def _last_assistant_id(transcript: str) -> str:
    """Use Claude's message identity, so repeated Stop hooks cannot repeat an alert."""
    try:
        path = Path(transcript).expanduser()
        if not path.is_file():
            return ""
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            file.seek(max(0, file.tell() - 262144))
            lines = file.read().splitlines()
        for raw in reversed(lines):
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if item.get("type") == "assistant":
                message = item.get("message") or {}
                return str(message.get("id") or item.get("uuid") or "")
    except OSError:
        pass
    return ""


def record(payload: dict) -> bool:
    session = str(payload.get("session_id") or "")
    if payload.get("hook_event_name") not in (None, "Stop"):
        return False
    # Stop can also mean Claude has yielded while background work is still running.
    # In that case a completion announcement would be exactly the old false positive.
    if any(str(task.get("status", "")).lower() in {"running", "pending", "queued"}
           for task in (payload.get("background_tasks") or [])):
        return False
    if any(not cron.get("recurring", False) for cron in (payload.get("session_crons") or [])):
        return False
    # prompt_id identifies the actual user turn. The transcript may lag the Stop hook, so
    # reading its last assistant message as the primary key can announce an earlier turn.
    prompt_id = str(payload.get("prompt_id") or "")
    message = prompt_id or _last_assistant_id(str(payload.get("transcript_path") or ""))
    if not session or not message:
        return False
    key = f"{session}:{message}"
    path = _events_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+", encoding="utf-8") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        file.seek(0)
        # The file is short in practice; scan the most recent events across all sessions.
        recent = file.readlines()[-500:]
        if any(json.loads(line).get("id") == key for line in recent if line.strip()):
            return False
        file.seek(0, os.SEEK_END)
        file.write(json.dumps({"id": key, "agent": "claude"}) + "\n")
        file.flush()
        os.fchmod(file.fileno(), 0o600)
    return True


def end_offset() -> int:
    try:
        return _events_file().stat().st_size
    except OSError:
        return 0


def read_since(offset: int) -> tuple[list[dict], int]:
    path = _events_file()
    try:
        with path.open("r", encoding="utf-8") as file:
            file.seek(offset if offset <= path.stat().st_size else 0)
            events = [json.loads(line) for line in file.read().splitlines() if line.strip()]
            return events, file.tell()
    except (OSError, ValueError):
        return [], offset


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            record(payload)
    except (ValueError, OSError):
        pass  # A hook must never interfere with Claude's own turn.


if __name__ == "__main__":
    main()
