"""3D reconstruction jobs: stable ids, stages, cancel and pause, a durable record.

The record holds what the job *is* — mode, state, version, metrics, a safe error category —
never pixels, OCR text or file paths of references (only their safe metadata). It survives a
restart so "how's the model going?" has a true answer, and a job interrupted by a restart is
marked failed-safely rather than left "building" forever.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from . import privacy
from .types import ErrorCategory, JobState, ReconstructionJob, STATE_LABEL, new_id


class Cancelled(Exception):
    pass


class Control:
    """Cancel and pause, checked between stages and between refinement iterations."""

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.resume = threading.Event()
        self.resume.set()

    def checkpoint(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()
        self.resume.wait()
        if self.cancel.is_set():
            raise Cancelled()


def _state_dir() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()


class JobBook:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (_state_dir() / "3d-jobs.json")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.path.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        rows = sorted(data.values(), key=lambda r: r.get("created", 0))[-30:]
        data = {r["id"]: r for r in rows}
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".3d-jobs-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=1, default=str)
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _safe(job: ReconstructionJob) -> dict:
        from ..selfrepair.jobs import scrub
        row = job.to_dict()
        row["request"] = scrub(job.request, 160)
        row["references"] = [r.safe() for r in job.references]
        row["stage_note"] = scrub(job.stage_note, 160)
        row["question"] = scrub(job.question, 200)
        return row

    def create(self, request: str) -> ReconstructionJob:
        job = ReconstructionJob(id=new_id("3d"), request=request)
        self.save(job)
        privacy.audit("job.created", job=job.id)
        return job

    def save(self, job: ReconstructionJob) -> None:
        job.updated = time.time()
        with self._locked():
            data = self._read()
            data[job.id] = self._safe(job)
            self._write(data)

    def get(self, job_id: str) -> Optional[dict]:
        return self._read().get(job_id)

    def all(self) -> list[dict]:
        return sorted(self._read().values(), key=lambda r: r.get("created", 0))

    def recover_interrupted(self) -> list[str]:
        """Jobs that were mid-flight when JARVIS stopped: mark them failed-safely."""
        out = []
        with self._locked():
            data = self._read()
            for row in data.values():
                if row.get("state") not in (s.value for s in JobState if s.terminal) and \
                        row.get("state") != JobState.NEEDS_INPUT.value:
                    row["state"] = JobState.FAILED.value
                    row["error"] = ErrorCategory.INTERNAL.value
                    row["stage_note"] = "interrupted by a restart; the last saved version is intact"
                    out.append(row["id"])
            if out:
                self._write(data)
        return out


def label(state: JobState) -> str:
    return STATE_LABEL[state]
