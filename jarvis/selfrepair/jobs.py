"""Repair jobs: a stable id, an explicit state, and only metadata that is safe to keep.

A job moves through these states, and only along the arrows in ``TRANSITIONS``:

    received → classified → gathering_evidence → reproducing → proposed → editing → testing
             → awaiting_activation → activating → health_checking → completed
    (any live state) → failed | cancelled | expired;   completed → rolled_back

What a job may hold: a one-line user-visible summary, where the request came from, the
component and the files it may touch, timestamps, test names and pass/fail counts, changed
file paths, the candidate commit, and the activation and rollback results. What it may not:
message bodies, dictated text, screen contents, tokens, anything from ``.env``. ``_scrub``
enforces the second list on every write — a summary is truncated, digit runs that look like
phone numbers are masked, and anything that looks like a key is replaced.

The store is one JSON file under the state dir, written atomically under a lock, so the
backend, the worker unit and the voice process all see the same jobs.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

RECEIVED = "received"
CLASSIFIED = "classified"
GATHERING = "gathering_evidence"
REPRODUCING = "reproducing"
PROPOSED = "proposed"
EDITING = "editing"
TESTING = "testing"
AWAITING = "awaiting_activation"
ACTIVATING = "activating"
HEALTH = "health_checking"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
ROLLED_BACK = "rolled_back"
EXPIRED = "expired"

TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED, ROLLED_BACK, EXPIRED})
LIVE = frozenset({RECEIVED, CLASSIFIED, GATHERING, REPRODUCING, PROPOSED, EDITING, TESTING,
                  ACTIVATING, HEALTH})

_ORDER = [RECEIVED, CLASSIFIED, GATHERING, REPRODUCING, PROPOSED, EDITING, TESTING, AWAITING,
          ACTIVATING, HEALTH, COMPLETED]
TRANSITIONS: dict[str, set[str]] = {s: {_ORDER[i + 1]} for i, s in enumerate(_ORDER[:-1])}
for _s in _ORDER[:-1]:
    TRANSITIONS[_s] |= {FAILED, CANCELLED, EXPIRED}
TRANSITIONS[PROPOSED] |= {AWAITING, COMPLETED}   # a diagnosis-only job ends at its finding
TRANSITIONS[GATHERING] |= {PROPOSED}         # nothing to reproduce: straight to a proposal
TRANSITIONS[TESTING] |= {AWAITING}
TRANSITIONS[ACTIVATING] |= {ROLLED_BACK}
TRANSITIONS[HEALTH] |= {ROLLED_BACK}
TRANSITIONS[COMPLETED] = {ROLLED_BACK}       # "undo your last repair"
for _s in (FAILED, CANCELLED, ROLLED_BACK, EXPIRED):
    TRANSITIONS[_s] = set()


class TransitionError(RuntimeError):
    pass


@dataclass
class TestRun:
    name: str                       # "focused before", "focused after", "regression", "static"
    command: str                    # the argv, joined — never output
    passed: int = 0
    failed: int = 0
    errors: int = 0
    ok: bool = False
    duration_s: float = 0.0
    failing: list[str] = field(default_factory=list)   # test ids only


@dataclass
class RepairJob:
    id: str
    summary: str
    source: str
    label: str
    state: str = RECEIVED
    component: str = ""
    allowed_files: list[str] = field(default_factory=list)
    tier: int = 1
    tier_reasons: list[str] = field(default_factory=list)
    protected_areas: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    history: list[dict] = field(default_factory=list)           # [{state, ts, note}]
    evidence: list[str] = field(default_factory=list)           # "ValueError at local_stt.py:118"
    tests: list[dict] = field(default_factory=list)             # TestRun rows
    changed_files: list[str] = field(default_factory=list)
    diff_lines: int = 0
    branch: str = ""
    worktree: str = ""
    base_commit: str = ""
    candidate_commit: str = ""
    policy_digest: str = ""
    activation: dict = field(default_factory=dict)
    rollback: dict = field(default_factory=dict)
    outcome: str = ""                                           # the sentence the owner hears
    worker: dict = field(default_factory=dict)                  # {"unit": ..., "pid": ...}
    cancel_requested: bool = False
    approval_id: str = ""
    announced: list[str] = field(default_factory=list)          # transitions already spoken

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def last_test(self, name: str) -> Optional[dict]:
        rows = [t for t in self.tests if t.get("name") == name]
        return rows[-1] if rows else None

    @classmethod
    def from_dict(cls, raw: dict) -> "RepairJob":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


# ----------------------------------------------------------------------------- scrubbing
_KEYLIKE = re.compile(
    r"(?:sk-[A-Za-z0-9_\-]{16,}|gsk_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9\-]{10,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)")
_PHONE = re.compile(r"\+?\d[\d\s\-]{8,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def scrub(text: str, limit: int = 200) -> str:
    text = _KEYLIKE.sub("<secret>", str(text or ""))
    text = _PHONE.sub("<number>", text)
    text = _EMAIL.sub("<email>", text)
    text = " ".join(text.split())
    return text[:limit]


def _scrub_job(job: RepairJob) -> RepairJob:
    job.summary = scrub(job.summary, 160)
    job.outcome = scrub(job.outcome, 400)
    job.evidence = [scrub(e, 160) for e in job.evidence[-20:]]
    job.tier_reasons = [scrub(r, 160) for r in job.tier_reasons[-10:]]
    for row in job.history:
        row["note"] = scrub(row.get("note", ""), 160)
    for key in ("activation", "rollback"):
        job.__dict__[key] = {k: (scrub(v, 300) if isinstance(v, str) else v)
                             for k, v in (getattr(job, key) or {}).items()}
    return job


# ----------------------------------------------------------------------------- store
def _state_dir() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()


class JobStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (_state_dir() / "repair-jobs.json")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.path.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        # Keep the newest 50; older finished jobs are history nobody asks about.
        rows = sorted(data.values(), key=lambda r: r.get("created", 0))[-50:]
        data = {r["id"]: r for r in rows}
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".repair-jobs-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=1, default=str)
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def create(self, summary: str, source: str, label: str, **fields: Any) -> RepairJob:
        job = RepairJob(id=time.strftime("%m%d") + "-" + secrets.token_hex(3),
                        summary=summary, source=source, label=label, **fields)
        job.history.append({"state": RECEIVED, "ts": job.created, "note": ""})
        with self._locked():
            data = self._read()
            data[job.id] = asdict(_scrub_job(job))
            self._write(data)
        return job

    def get(self, job_id: str) -> Optional[RepairJob]:
        raw = self._read().get(job_id)
        return RepairJob.from_dict(raw) if raw else None

    def all(self) -> list[RepairJob]:
        return sorted((RepairJob.from_dict(r) for r in self._read().values()), key=lambda j: j.created)

    def latest(self, *, live_only: bool = False) -> Optional[RepairJob]:
        jobs = [j for j in self.all() if not live_only or not j.terminal]
        return jobs[-1] if jobs else None

    def update(self, job_id: str, **fields: Any) -> RepairJob:
        with self._locked():
            data = self._read()
            raw = data.get(job_id)
            if raw is None:
                raise KeyError(job_id)
            job = RepairJob.from_dict(raw)
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated = time.time()
            data[job_id] = asdict(_scrub_job(job))
            self._write(data)
            return job

    def transition(self, job_id: str, state: str, note: str = "", **fields: Any) -> RepairJob:
        """Move a job to `state`, or raise TransitionError if the arrow does not exist."""
        with self._locked():
            data = self._read()
            raw = data.get(job_id)
            if raw is None:
                raise KeyError(job_id)
            job = RepairJob.from_dict(raw)
            if state not in TRANSITIONS.get(job.state, set()):
                raise TransitionError(f"{job.id}: {job.state} → {state} is not allowed")
            job.state = state
            job.updated = time.time()
            job.history.append({"state": state, "ts": job.updated, "note": note})
            for key, value in fields.items():
                setattr(job, key, value)
            data[job_id] = asdict(_scrub_job(job))
            self._write(data)
            return job

    def expire_stale(self, ttl_s: float, now: Optional[float] = None) -> list[str]:
        now = time.time() if now is None else now
        expired = []
        for job in self.all():
            if job.state in LIVE and now - job.created > ttl_s:
                try:
                    self.transition(job.id, EXPIRED, "ran past its time limit",
                                    outcome="That repair ran out of time, so I stopped it. Nothing was activated.")
                    expired.append(job.id)
                except TransitionError:
                    pass
        return expired


# ----------------------------------------------------------------------------- audit
_AUDIT_FIELDS = ("job", "label", "component", "source", "tier", "state", "areas", "reason", "commit", "ok")


def audit(event: str, **fields: Any) -> None:
    """One line per decision, in selfrepair-audit.jsonl. Only the whitelisted fields, never the
    words of a request: a label ("prohibited"), a component, a source — not what was said."""
    row: dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event[:40]}
    for key in _AUDIT_FIELDS:
        if key in fields and fields[key] is not None:
            value = fields[key]
            row[key] = [scrub(v, 60) for v in value][:8] if isinstance(value, (list, tuple)) else \
                (value if isinstance(value, (bool, int)) else scrub(value, 80))
    path = _state_dir() / "selfrepair-audit.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass
