"""The process a repair runs in, and how it is started.

A repair is never run inside the backend or the voice loop: it would freeze them, and an
activation that restarts the backend would kill it half-way. It runs as its own process —
by preference a transient systemd user unit, which puts it outside the backend's cgroup and
gives it hard limits the kernel enforces:

    MemoryMax  TasksMax (every subprocess counts)  RuntimeMaxSec  CPUQuota

Where systemd is not available it is a detached process group instead, and the per-command
rlimits in sandbox.run still apply. Always started from, and importing, the running checkout.

    python -m jarvis.selfrepair.worker run <job-id>        diagnose → … → awaiting/activation
    python -m jarvis.selfrepair.worker activate <job-id>   an approved activation
    python -m jarvis.selfrepair.worker undo <job-id>       "undo your last repair"
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Optional

from . import jobs, policy
from .jobs import JobStore, RepairJob

ACTIONS = ("run", "activate", "undo")
_PASS_ENV = ("JARVIS_STATE_DIR", "XDG_RUNTIME_DIR", "XDG_STATE_HOME", "JARVIS_WEB_PORT", "HOME", "PATH",
             "LANG", "JARVIS_SELF_REPAIR_DISABLED", "JARVIS_REPAIR_EDITOR", "JARVIS_REPAIR_ROOT",
             "JARVIS_REPAIR_AUTO_ACTIVATE", "JARVIS_REPAIR_REGRESSION", "JARVIS_REPAIR_MAX_DIFF_LINES",
             "JARVIS_REPAIR_TEST_TIMEOUT", "JARVIS_REPAIR_EDIT_TIMEOUT")


def _mode() -> str:
    chosen = os.environ.get("JARVIS_REPAIR_LAUNCH", "").strip().lower()
    if chosen in {"systemd", "process", "inline"}:
        return chosen
    if shutil.which("systemd-run") and os.environ.get("XDG_RUNTIME_DIR"):
        return "systemd"
    return "process"


def unit_name(job_id: str, action: str) -> str:
    return f"jarvis-repair-{job_id}-{action}"


def launch(job_id: str, action: str = "run", store: Optional[JobStore] = None) -> dict:
    """Start the worker for `job_id`. Returns {"mode", "unit"|"pid"} — also recorded on the job."""
    if action not in ACTIONS:
        raise ValueError(action)
    store = store or JobStore()
    mode = _mode()
    argv = [policy.venv_python(), "-m", "jarvis.selfrepair.worker", action, job_id]
    info: dict = {"mode": mode, "action": action}
    if mode == "inline":
        main([action, job_id], store=store)
        return info
    if mode == "systemd":
        lim = policy.LIMITS
        unit = unit_name(job_id, action)
        cmd = ["systemd-run", "--user", "--collect", "--quiet", "--unit", unit,
               "-p", f"MemoryMax={lim.worker_memory}", "-p", f"TasksMax={lim.tasks_max}",
               "-p", f"RuntimeMaxSec={lim.worker_runtime_s}", "-p", "CPUQuota=300%",
               "-p", "Nice=10", "--working-directory", str(policy.REPO)]
        for key in _PASS_ENV:
            if key in os.environ:
                cmd += ["-E", f"{key}={os.environ[key]}"]
        out = subprocess.run(cmd + argv, capture_output=True, text=True, timeout=30, check=False)
        if out.returncode == 0:
            info["unit"] = unit
            store.update(job_id, worker=info)
            return info
        mode = info["mode"] = "process"      # systemd refused (no user bus?) — fall back honestly
    proc = subprocess.Popen(argv, cwd=str(policy.REPO), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    info["pid"] = proc.pid
    store.update(job_id, worker=info)
    return info


def stop(job: RepairJob) -> None:
    """Hard-stop a worker that did not stop itself after a cancel."""
    info = job.worker or {}
    if info.get("unit"):
        subprocess.run(["systemctl", "--user", "stop", info["unit"]], capture_output=True, timeout=30, check=False)
    elif info.get("pid"):
        import signal

        try:
            os.killpg(int(info["pid"]), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, ValueError):
            pass


def _emit(job: RepairJob, text: str) -> None:
    """A compact status line for the overlay. Speech is the voice loop's job (it reads the store)."""
    from ..settings.runtime import _emit as emit

    emit("repair", json.dumps({"id": job.id, "state": job.state, "component": job.component,
                               "text": text[:200]}))


def build_pipeline(store: JobStore):
    from .activate import Activator
    from .editor import default_editor
    from .pipeline import Pipeline

    return Pipeline(store, policy.REPO, default_editor(), Activator(policy.REPO), notify=_emit)


def main(argv: Optional[list[str]] = None, store: Optional[JobStore] = None, pipeline=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] not in ACTIONS:
        print("usage: python -m jarvis.selfrepair.worker run|activate|undo <job-id>", file=sys.stderr)
        return 2
    action, job_id = argv
    store = store or JobStore()
    job = store.get(job_id)
    if job is None:
        return 1
    if policy.disabled():
        if not job.terminal and job.state != jobs.AWAITING:
            store.transition(job_id, jobs.FAILED, "self-repair disabled",
                             outcome="Self-repair is switched off, so I didn't change anything.")
        return 0
    pipeline = pipeline or build_pipeline(store)
    if action == "run":
        pipeline.run(job_id)
    elif action == "activate":
        pipeline.activate(job_id, approved=True)
    else:
        undo(job_id, store, pipeline)
    return 0


def undo(job_id: str, store: JobStore, pipeline) -> RepairJob:
    job = store.get(job_id)
    component = policy.component_named(job.component) if job else None
    if job is None or job.state != jobs.COMPLETED or component is None or pipeline.activator is None:
        return job
    result = pipeline.activator.undo(job.candidate_commit, list(component.services))
    rollback = {"ok": bool(result.rollback_ok), "commit": result.rollback_commit,
                "health": result.rollback_health, "error": result.error}
    if result.ok:
        job = store.transition(job_id, jobs.ROLLED_BACK, "undone by request", rollback=rollback,
                               outcome=f"I've undone the {component.name} repair and the services are healthy on the previous version.")
    else:
        job = store.update(job_id, rollback=rollback,
                           outcome=f"I couldn't undo the {component.name} repair: {result.error or 'the rollback did not come back healthy'}.")
    pipeline.notify(job, job.outcome)
    return job


if __name__ == "__main__":
    raise SystemExit(main())
