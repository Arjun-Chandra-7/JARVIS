"""Cancel background work, and be honest about what could not be stopped.

Cancellation that silently does nothing is worse than no cancel button. Queued work can always be
dropped. Work already running is a real external process — a coding agent mid-edit, a research
request in flight — and stopping it is a request, not a guarantee.

So this reports three separate numbers: what was dropped from the queue, what was signalled, and
what is still running despite being asked to stop. The UI shows exactly that rather than claiming
everything stopped.
"""

from __future__ import annotations

import os
import signal
from typing import Any

# Terminal states, borrowed from the coding-job manager so the two agree on what "finished" means.
ACTIVE = {"queued", "running", "pending", "unknown"}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def cancel_all() -> dict[str, Any]:
    """Stop what can be stopped. Returns a report the UI can state plainly."""
    dropped: list[str] = []
    signalled: list[str] = []
    stubborn: list[str] = []
    errors: list[str] = []

    try:
        from ..integrations import coding_jobs
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "cancelled": 0, "message": f"Job manager unavailable: {exc}"}

    try:
        manager = coding_jobs.get_manager()
        jobs = list(getattr(manager, "jobs", {}).values())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "cancelled": 0, "message": f"Could not read the job list: {exc}"}

    for job in jobs:
        status = (getattr(job, "status", "") or "").lower()
        if status not in ACTIVE:
            continue
        jid = getattr(job, "id", "?")
        pid = getattr(job, "pid", None)

        if not pid:
            # Never started a process: dropping it from the queue is a real, complete cancel.
            try:
                job.status = "cancelled"
                dropped.append(jid)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{jid}: {exc}")
            continue

        try:
            os.kill(int(pid), signal.SIGTERM)
            signalled.append(jid)
        except ProcessLookupError:
            job.status = "cancelled"
            dropped.append(jid)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{jid}: {exc}")
            stubborn.append(jid)

    # Give anything signalled a moment, then check whether it actually went.
    if signalled:
        import time

        time.sleep(0.4)
        for job in jobs:
            jid = getattr(job, "id", "?")
            if jid not in signalled:
                continue
            pid = getattr(job, "pid", None)
            if pid and _pid_alive(int(pid)):
                stubborn.append(jid)
            else:
                try:
                    job.status = "cancelled"
                except Exception:  # noqa: BLE001
                    pass

    try:
        manager.save()
    except Exception:  # noqa: BLE001
        pass

    stopped = len(dropped) + len([j for j in signalled if j not in stubborn])
    if not jobs or (not dropped and not signalled):
        message = "Nothing was running."
    elif stubborn:
        message = (
            f"Stopped {stopped}. {len(stubborn)} did not stop when asked and may still be "
            f"running — check the Tasks view."
        )
    else:
        message = f"Stopped {stopped} task{'s' if stopped != 1 else ''}."
    if errors:
        message += f" ({len(errors)} could not be signalled.)"

    return {
        "ok": not stubborn and not errors,
        "cancelled": stopped,
        "dropped": dropped,
        "signalled": signalled,
        "still_running": stubborn,
        "message": message,
    }
