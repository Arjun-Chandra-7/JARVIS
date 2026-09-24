"""The one away session, and the private file it lives in.

Everything away mode knows is kept in ``<vault>/Jarvis/private/away/state.json`` (0600): the
session and its policy, one bounded record per conversation, the ids already seen, the ids of
messages Jarvis itself sent, the alerts waiting to be spoken, and an archive of past briefings.
Two processes read it — the backend, which runs the responder, and the voice process, which only
reads whether away mode is on and speaks the alerts — so every write happens under a file lock.

Message bodies are not kept. A thread holds a short redacted gist of what was asked, the details
collected and what the owner has to do; the raw recent turns needed for the next reply live in
memory only (see engine.py).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

PENDING, ACTIVE, ENDED, EXPIRED, CANCELLED = "pending_approval", "active", "ended", "expired", "cancelled"

# What each reply policy means for a thread.
CONVERSE, TAKE_MESSAGE, SILENT = "converse", "take_message", "silent"

_SEEN_CAP, _OUTBOUND_CAP, _ARCHIVE_CAP, _ALERT_CAP = 3000, 1000, 30, 50


def local_tz() -> str:
    """The IANA name of the machine's timezone, or its abbreviation when that is all there is."""
    env = os.environ.get("TZ", "").strip()
    if env:
        return env
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return datetime.now().astimezone().tzname() or "UTC"


def tzinfo(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - an abbreviation or an unknown zone: use the machine's
        return datetime.now().astimezone().tzinfo


def clock(ts: float, tz: str) -> str:
    """'8 PM', '8:30 PM' — how a time is said, in the session's timezone."""
    moment = datetime.fromtimestamp(ts, tzinfo(tz))
    text = moment.strftime("%I:%M %p").lstrip("0")
    return text.replace(":00 ", " ")


@dataclass
class AwaySession:
    session_id: str = field(default_factory=lambda: "away-" + secrets.token_hex(4))
    owner_id: str = "owner"
    owner_name: str = ""
    start_time: float = field(default_factory=time.time)
    planned_end_time: Optional[float] = None
    actual_end_time: Optional[float] = None
    timezone: str = field(default_factory=local_tz)
    status: str = PENDING
    # Platforms Jarvis replies on; muted ones are counted in the briefing and nothing else.
    allowed_platforms: list[str] = field(default_factory=lambda: ["whatsapp"])
    muted_platforms: list[str] = field(default_factory=list)
    # Canonical contact ids, or "group:family" / "group:vip". Empty = everyone not blocked.
    allowed_contacts: list[str] = field(default_factory=list)
    blocked_contacts: list[str] = field(default_factory=list)
    contact_groups: dict[str, list[str]] = field(default_factory=dict)
    reply_groups: bool = False             # group chats are never answered unless the owner says so
    reply_policy: str = CONVERSE
    call_policy: str = "record"            # record | text_back | answer (only with a real bridge)
    disclosure_text: str = ""
    language_preferences: list[str] = field(default_factory=lambda: ["en", "hinglish", "hi"])
    allowed_topics: list[str] = field(default_factory=lambda: ["availability", "return_time", "take_message"])
    restricted_topics: list[str] = field(default_factory=lambda: [
        "payment", "secret", "private_info", "legal", "purchase", "commitment", "sensitive",
        "media", "account_recovery", "location", "contact_others", "policy_change"])
    escalation_rules: dict[str, Any] = field(default_factory=lambda: {
        "interrupt": "urgent_only", "spoken": True, "desktop": True, "phone": True, "vip": []})
    maximum_turns_per_thread: int = 6
    maximum_reply_rate: int = 30           # replies per hour, all threads together
    quiet_hours: list[str] = field(default_factory=list)   # ["22:30", "07:00"]: no spoken alerts
    live_summary: dict[str, Any] = field(default_factory=dict)
    pending_escalations: list[dict[str, Any]] = field(default_factory=list)
    created_from: str = "voice"
    approval_id: str = ""
    dry_run: bool = False
    taken_over: list[str] = field(default_factory=list)    # thread keys the owner is handling

    def __post_init__(self) -> None:
        if not self.live_summary:
            self.live_summary = blank_summary()

    # ------------------------------------------------------------------ state
    @property
    def active(self) -> bool:
        return self.status == ACTIVE

    def expired_at(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return self.planned_end_time is not None and now >= self.planned_end_time

    def return_clock(self) -> str:
        return clock(self.planned_end_time, self.timezone) if self.planned_end_time else ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AwaySession":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def blank_summary() -> dict[str, Any]:
    return {"urgent": [], "needs_action": [], "handled": [], "calls": [], "bots": [],
            "suppressed": {"group": 0, "duplicate": 0, "muted": 0, "blocked": 0, "outside_policy": 0,
                           "bot": 0, "rate_limited": 0},
            "failed": [], "counts": {"received": 0, "replied": 0, "threads": 0}}


@dataclass
class ThreadState:
    platform: str
    thread_id: str
    participants: list[str] = field(default_factory=list)        # display names only
    contact_ids: list[str] = field(default_factory=list)
    is_group: bool = False
    disclosure_sent: bool = False
    disclosed_to: list[str] = field(default_factory=list)        # group participants told who we are
    language: str = "en"
    summary: str = ""                                            # structured gist, redacted
    current_request: str = ""
    collected: list[str] = field(default_factory=list)           # details worth passing on
    promises_avoided: list[str] = field(default_factory=list)    # restricted topics declined
    pending_question: str = ""
    urgency: str = "normal"                                      # normal | important | urgent | emergency
    turn_count: int = 0                                          # replies sent by Jarvis
    incoming_count: int = 0
    model_calls: int = 0
    last_activity: float = field(default_factory=time.time)
    last_reply_at: float = 0.0
    owner_action_required: bool = False
    status: str = "open"                                         # open | closed | owner | blocked | bot | capped
    who: str = ""

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.thread_id}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThreadState":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


# ---------------------------------------------------------------------------- the file

def state_dir(config) -> Path:
    return Path(config.vault_path) / "Jarvis" / "private" / "away"


def _blank_state() -> dict[str, Any]:
    return {"session": None, "threads": {}, "seen": {}, "outbound": {}, "alerts": [], "archive": [],
            "recent": {}, "fast": {},
            "prefs": {"vip": [], "retention_days": 14, "thread_ttl_s": 6 * 3600, "store_message_text": False}}


class Store:
    """Read-modify-write of the state file under an exclusive lock."""

    def __init__(self, config) -> None:
        self.config = config
        self.dir = state_dir(config)
        self.path = self.dir / "state.json"

    def _ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.dir, 0o700)

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _blank_state()
        state = _blank_state()
        if isinstance(data, dict):
            state.update({k: v for k, v in data.items() if k in state})
            state["prefs"] = {**_blank_state()["prefs"], **(data.get("prefs") or {})}
        return state

    @contextlib.contextmanager
    def edit(self) -> Iterator[dict[str, Any]]:
        self._ensure_dir()
        lock_path = self.dir / ".lock"
        with open(lock_path, "a+") as lock:
            with contextlib.suppress(OSError):
                os.chmod(lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = self.read()
                yield state
                self._prune(state)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                with contextlib.suppress(OSError):
                    os.chmod(tmp, 0o600)
                tmp.replace(self.path)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _prune(state: dict[str, Any]) -> None:
        now = time.time()
        for key, cap in (("seen", _SEEN_CAP), ("outbound", _OUTBOUND_CAP)):
            items = state.get(key) or {}
            fresh = {k: v for k, v in items.items() if now - float(v if not isinstance(v, dict) else v.get("at", 0)) < 3 * 86400}
            if len(fresh) > cap:
                fresh = dict(sorted(fresh.items(), key=lambda kv: float(kv[1] if not isinstance(kv[1], dict) else kv[1].get("at", 0)))[-cap:])
            state[key] = fresh
        state["alerts"] = (state.get("alerts") or [])[-_ALERT_CAP:]
        state["recent"] = {k: [t for t in v if now - t < 900] for k, v in (state.get("recent") or {}).items()
                           if any(now - t < 900 for t in v)}
        days = float((state.get("prefs") or {}).get("retention_days", 14))
        state["archive"] = [a for a in (state.get("archive") or [])
                            if now - float(a.get("ended", now)) < days * 86400][-_ARCHIVE_CAP:]

    # ------------------------------------------------------------------ helpers
    def session(self) -> Optional[AwaySession]:
        raw = self.read().get("session")
        return AwaySession.from_dict(raw) if raw else None

    def active_session(self) -> Optional[AwaySession]:
        s = self.session()
        return s if s and s.active and not s.expired_at() else None

    def clear_history(self) -> int:
        """Forget every thread, briefing and alert. The live session's policy is kept."""
        with self.edit() as state:
            n = len(state["threads"]) + len(state["archive"])
            state["threads"], state["archive"], state["alerts"], state["recent"] = {}, [], [], {}
            if state.get("session"):
                state["session"]["live_summary"] = blank_summary()
                state["session"]["pending_escalations"] = []
        return n


def is_active(config) -> bool:
    """Cheap check for other processes: is an approved session running right now?"""
    return Store(config).active_session() is not None
