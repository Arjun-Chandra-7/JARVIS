"""Which repair news is worth saying out loud, said once.

The voice loop asks every few seconds. Only meaningful transitions are spoken — a fix about to
be applied, one waiting for approval, and how it ended — never "running the tests" on a loop.
Only a job's *current* state is considered, so a tier-1 repair that passes through "awaiting"
on its way to "activating" is announced once, as the activation. Each announcement is marked in
the job store, so a voice process restarted by the activation itself does not repeat it.
"""
from __future__ import annotations

import time
from typing import Optional

from . import jobs
from .jobs import JobStore

SPOKEN = (jobs.AWAITING, jobs.ACTIVATING, jobs.COMPLETED, jobs.FAILED, jobs.ROLLED_BACK, jobs.EXPIRED)


def _line(job: jobs.RepairJob) -> str:
    if job.state == jobs.ACTIVATING:
        return f"Applying a verified repair to {job.component} now."
    return job.outcome or ""


def due(store: Optional[JobStore] = None, *, window_s: float = 900, now: Optional[float] = None) -> list[str]:
    store = store or JobStore()
    now = time.time() if now is None else now
    out: list[str] = []
    for job in store.all():
        if job.state not in SPOKEN or job.state in job.announced or now - job.updated > window_s:
            continue
        # A cancel was already answered when it was asked for; it is not news.
        if job.state == jobs.CANCELLED:
            continue
        text = _line(job)
        store.update(job.id, announced=job.announced + [job.state])
        if text:
            out.append(text)
    return out
