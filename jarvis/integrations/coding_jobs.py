"""Durable orchestration for local coding CLIs.

The manager deliberately owns process launching and status, rather than pretending it can inspect
every provider's private session state.  External runners can report lifecycle updates through
``record_event`` (for example from a hook or a bridge) and those updates survive a Jarvis restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


PROVIDERS = ("codex", "claude", "agy")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TERMINAL = {"completed", "failed", "cancelled"}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _changed_files(before: dict[str, Any], after: dict[str, Any]) -> int:
    """How many working-tree paths differ between two workspace_snapshot() calls."""
    b = {line[3:] for line in (before.get("status") or []) if len(line) > 3}
    a = {line[3:] for line in (after.get("status") or []) if len(line) > 3}
    return len(a ^ b) or len(a)


def summarize(job: "CodingJob") -> str:
    """A short, deterministic, human-readable result line — no LLM call.

    Prefers the tail of real CLI output; annotates with the git working-tree delta so
    "Claude returned with: 3 files changed. <tail>" is meaningful even when output is noisy.
    """
    body = _ANSI_RE.sub("", (job.output or "").strip())
    tail = " ".join(line.strip() for line in body.splitlines() if line.strip())[-360:].strip()
    n = _changed_files(job.before or {}, job.after or {})
    delta = f"{n} file{'s' if n != 1 else ''} changed. " if n else ""
    if job.status == "failed":
        err = _ANSI_RE.sub("", (job.error or "").strip()).splitlines()
        why = (err[-1] if err else tail) or "no output"
        return f"failed — {why[-300:]}"
    if job.status == "cancelled":
        return "cancelled before it finished"
    return (delta + (tail or "no notable output")).strip()


# Provider invocations that are editor/IDE plumbing or one-shot queries, not a coding task.
_NOT_A_JOB = re.compile(r"\b(?:app-server|mcp|--mcp|lsp|--version|--help|completion|doctor|"
                        r"config\s+(?:get|set|list)|whoami|login|logout|update|upgrade)\b")


def scan_provider_processes() -> list[dict[str, Any]]:
    """Best-effort list of running provider CLIs (a real task or interactive TUI) with a cwd.

    Process instrumentation only — never infers state from a window appearing/closing. Skips
    IDE background servers and one-shot subcommands so the HUD shows no phantom jobs.
    """
    if not shutil.which("pgrep"):
        return []
    try:
        out = subprocess.run(["pgrep", "-a", "-f", r"(^|/)(codex|claude|agy)([[:space:]]|$)"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[dict[str, Any]] = []
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        pid, cmdline = int(parts[0]), parts[1]
        argv0 = cmdline.split(None, 1)[0].rsplit("/", 1)[-1]
        provider = argv0 if argv0 in PROVIDERS else next(
            (p for p in PROVIDERS if re.search(rf"(^|/){p}([ ]|$)", cmdline)), None)
        if not provider or _NOT_A_JOB.search(cmdline) or ".vscode/extensions" in cmdline:
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = ""
        found.append({"pid": pid, "provider": provider, "workspace": cwd, "cmdline": cmdline[:400]})
    return found


def _clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD"}}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def available_providers() -> dict[str, bool]:
    """Return only local CLI availability; no command is launched."""
    return {provider: bool(shutil.which(provider)) for provider in PROVIDERS}


def normalize_selection(
    text: str, provider: str | None = None, model: str | None = None, effort: str | None = None,
) -> dict[str, str | None]:
    """Extract a conversational provider/model/effort choice without changing settings."""
    words = (text or "").lower()
    picked_provider = (provider or "").strip().lower() or None
    if not picked_provider:
        picked_provider = next((p for p in PROVIDERS if p in words), None)
    if picked_provider and picked_provider not in PROVIDERS:
        raise ValueError(f"Unsupported coding provider: {picked_provider}")

    picked_effort = (effort or "").strip().lower() or None
    if not picked_effort:
        aliases = {"quick": "low", "fast": "low", "balanced": "medium", "deep": "high", "maximum": "max"}
        picked_effort = next((value for key, value in aliases.items() if key in words), None)
        if not picked_effort:
            # Match whole words and longest values first: ``xhigh`` must not become ``high``.
            picked_effort = next((value for value in sorted(EFFORTS, key=len, reverse=True)
                                  if re.search(rf"\b{re.escape(value)}\b", words)), None)
    if picked_effort and picked_effort not in EFFORTS:
        raise ValueError(f"Unsupported effort: {picked_effort}")
    # Models are provider-specific strings.  Callers may pass an explicit value; prose is not
    # guessed because a wrong model name makes a coding request fail much later.
    return {"provider": picked_provider, "model": (model or "").strip() or None, "effort": picked_effort}


def build_command(provider: str, prompt: str, workspace: str, model: str | None = None, effort: str | None = None) -> list[str]:
    """Build a non-interactive, workspace-scoped command with normal safety prompts enabled."""
    provider = (provider or "").strip().lower()
    effort = (effort or "").strip().lower() or None
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported coding provider: {provider}")
    if not prompt.strip():
        raise ValueError("A coding prompt is required.")
    if effort and effort not in EFFORTS:
        raise ValueError(f"Unsupported effort: {effort}")
    if provider == "codex":
        command = ["codex", "--ask-for-approval", "on-request", "exec", "--cd", workspace,
                   "--sandbox", "workspace-write"]
        if model:
            command += ["--model", model]
        if effort:
            command[1:1] = ["-c", f'model_reasoning_effort="{effort}"']
        return command + [prompt]
    if provider == "claude":
        command = ["claude", "--print", "--permission-mode", "acceptEdits", "--permission-prompts", "none"]
        if model:
            command += ["--model", model]
        if effort:
            command += ["--effort", effort]
        return command + [prompt]
    # agy supports low/medium/high only; preserve a user preference while using its nearest safe level.
    agy_effort = {"xhigh": "high", "max": "high"}.get(effort or "", effort or "medium")
    command = ["agy", "--new-project", "--mode", "accept-edits", "--effort", agy_effort, "--print"]
    if model:
        command += ["--model", model]
    return command + [prompt]


def workspace_snapshot(workspace: str | Path) -> dict[str, Any]:
    """Capture a compact before/after audit record without modifying the workspace."""
    path = Path(workspace).expanduser().resolve()
    snap: dict[str, Any] = {"workspace": str(path), "captured_at": _now(), "exists": path.is_dir(), "git": False}
    if not path.is_dir():
        return snap
    try:
        root = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if root.returncode:
            return snap
        git_root = root.stdout.strip()
        head = subprocess.run(["git", "-C", git_root, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        status = subprocess.run(["git", "-C", git_root, "status", "--porcelain=v1"], capture_output=True, text=True, timeout=5)
        snap.update({"git": True, "git_root": git_root, "head": head.stdout.strip() if not head.returncode else None,
                     "status": status.stdout.splitlines()[:200]})
    except (OSError, subprocess.SubprocessError):
        pass
    return snap


@dataclass
class CodingJob:
    id: str
    prompt: str
    workspace: str
    provider: str
    model: str | None = None
    effort: str | None = None
    status: str = "queued"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    pid: int | None = None
    worker_pid: int | None = None
    timeout_s: int = 900
    command: list[str] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    error: str = ""
    summary: str = ""
    external: bool = False
    announced: bool = False   # set once a completion has been spoken/sounded, so it fires once
    events: list[dict[str, str]] = field(default_factory=list)


Runner = Callable[[list[str], str], Awaitable[tuple[int, str, str]]]


async def _run_process(command: list[str], workspace: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(*command, cwd=workspace, env=_clean_env(),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class CodingJobManager:
    """Persistent background jobs. Use one manager per Jarvis process and call ``list_jobs`` for HUD polling."""

    def __init__(self, state_path: str | Path | None = None, runner: Runner | None = None) -> None:
        self.state_path = Path(state_path or "~/.config/jarvis/coding-jobs.json").expanduser()
        self.runner = runner or _run_process
        self.jobs: dict[str, CodingJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.selections: dict[str, dict[str, str | None]] = {}
        self.ws_selections: dict[str, dict[str, str | None]] = {}  # workspace -> last provider/model/effort
        self.pending: dict[str, Any] = {}
        self._subscribers: list[Callable[[dict[str, Any]], Any]] = []
        self._dirty_jobs: set[str] = set()
        self._dirty_sessions: set[str] = set()
        self._load()

    @property
    def _lock_path(self) -> Path:
        return self.state_path.with_suffix(self.state_path.suffix + ".lock")

    def _read_disk(self) -> dict[str, Any]:
        empty = {"jobs": [], "selections": {}, "ws_selections": {}, "pending": {}}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {**empty, "jobs": raw} if isinstance(raw, list) else {**empty, **raw}
        except (OSError, ValueError, TypeError):
            return empty

    def _load(self) -> None:
        try:
            raw = self._read_disk()
            # Accept the first version's plain job list as well as the versioned envelope.
            records = raw.get("jobs", [])
            self.selections = raw.get("selections", {})
            self.ws_selections = raw.get("ws_selections", {})
            self.pending = raw.get("pending", {})
            for record in records:
                job = CodingJob(**record)
                # A subprocess cannot be reliably reattached after a process restart. Keep it
                # visible and require an opt-in hook/event to establish a final result.
                if job.status in {"queued", "running"}:
                    alive = bool((job.pid and _pid_alive(job.pid)) or (job.worker_pid and _pid_alive(job.worker_pid)))
                    job.status = "running" if alive else "unknown"
                    job.updated_at = _now()
                    msg = "Detached worker is still running." if alive else "Worker was not found after restart."
                    job.events.append({"at": job.updated_at, "kind": "recovered", "message": msg})
                self.jobs[job.id] = job
        except (OSError, ValueError, TypeError):
            return

    def _save(self) -> None:
        """Lock and merge dirty records, so detached workers cannot overwrite other jobs."""
        import fcntl

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            disk = self._read_disk()
            jobs = {row.get("id"): row for row in disk.get("jobs", []) if row.get("id")}
            jobs.update({job_id: asdict(self.jobs[job_id]) for job_id in self._dirty_jobs if job_id in self.jobs})
            selections, pending = dict(disk.get("selections", {})), dict(disk.get("pending", {}))
            for session_id in self._dirty_sessions:
                if session_id in self.selections:
                    selections[session_id] = self.selections[session_id]
                else:
                    selections.pop(session_id, None)
                if session_id in self.pending:
                    pending[session_id] = self.pending[session_id]
                else:
                    pending.pop(session_id, None)
            ws_selections = {**disk.get("ws_selections", {}), **self.ws_selections}
            tmp = self.state_path.with_suffix(self.state_path.suffix + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"jobs": list(jobs.values()), "selections": selections,
                                      "ws_selections": ws_selections, "pending": pending}, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self._dirty_jobs.clear()
        self._dirty_sessions.clear()

    def refresh(self) -> list[dict[str, Any]]:
        """Refresh durable worker results for HUD polling; callbacks are process-local."""
        changed = []
        for record in self._read_disk().get("jobs", []):
            try:
                candidate = CodingJob(**record)
            except TypeError:
                continue
            current = self.jobs.get(candidate.id)
            if current is None or candidate.updated_at >= current.updated_at:
                if current is None or asdict(candidate) != asdict(current):
                    changed.append(asdict(candidate))
                self.jobs[candidate.id] = candidate
        return changed

    def _event(self, job: CodingJob, kind: str, message: str = "") -> None:
        job.updated_at = _now()
        job.events.append({"at": job.updated_at, "kind": kind, "message": message[:1000]})
        self._dirty_jobs.add(job.id)
        update = {"type": "coding_job", "job": asdict(job), "event": job.events[-1]}
        for subscriber in list(self._subscribers):
            try:
                result = subscriber(update)
                if hasattr(result, "__await__"):
                    asyncio.create_task(result)
            except Exception:
                continue

    def subscribe(self, callback: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        """Receive local job/event updates. Returns an unsubscribe function for web/SSE wiring."""
        self._subscribers.append(callback)
        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
        return unsubscribe

    async def handle_message(self, text: str, session_id: str = "local") -> str | None:
        """Handle conversational provider selection before an LLM sees the message.

        It intentionally does not launch a job: the command router supplies the confirmed task and
        workspace to ``start``.  This makes speech selection safe and testable.
        """
        spoken = (text or "").strip()
        low = spoken.lower()
        if not spoken:
            return None
        pending = self.pending.get(session_id)
        if low in {"cancel", "cancel coding", "never mind", "nevermind", "stop coding"} and pending:
            self.pending.pop(session_id, None)
            self._dirty_sessions.add(session_id)
            self._save()
            return "Coding request cancelled."
        if isinstance(pending, str):  # migrate short-lived first implementation state
            pending = {"stage": pending}
            self.pending[session_id] = pending
        if isinstance(pending, dict):
            stage = pending.get("stage")
            if stage == "provider":
                choice = normalize_selection(spoken)
                if choice["provider"]:
                    pending["provider"] = choice["provider"]
                    pending["stage"] = "model"
                    self._dirty_sessions.add(session_id)
                    self._save()
                    return "Which model should I use? Say default to use the provider default."
                return "Choose Codex, Claude, or agy."
            if stage == "model":
                if low in {"default", "provider default", "no preference"}:
                    pending["model"] = None
                else:
                    model_match = re.search(r"(?:model\s+)?([a-z0-9][a-z0-9._/-]{1,80})", low)
                    if not model_match:
                        return "Name a model, or say default."
                    pending["model"] = model_match.group(1)
                pending["stage"] = "effort"
                self._dirty_sessions.add(session_id)
                self._save()
                return "What effort: low, medium, high, xhigh, or max?"
            if stage == "effort":
                choice = normalize_selection(spoken)
                if not choice["effort"]:
                    return "Choose low, medium, high, xhigh, or max effort."
                pending["effort"] = choice["effort"]
                self._dirty_sessions.add(session_id)
                self._save()
                try:
                    job = await self.start(pending["task"], pending["workspace"], pending["provider"],
                                           pending.get("model"), pending["effort"])
                except Exception as exc:
                    return f"I couldn't start that coding job: {exc}"
                choice = {key: pending.get(key) for key in ("provider", "model", "effort")}
                self.selections[session_id] = choice
                self.ws_selections[str(pending["workspace"])] = choice  # remember per folder — no re-asking
                self.pending.pop(session_id, None)
                self._dirty_sessions.add(session_id)
                self._save()
                return (f"Prompt given, sir. {job['provider'].title()} job {job['id']} is running. "
                        "I'll use these settings for this folder from now on.")
        selection_request = bool(re.search(r"\b(?:use|switch|set)\s+(?:the\s+)?(?:coding\s+)?(?:provider|agent)?\s*(codex|claude|agy)\b", low))
        if "which" in low and ("coding provider" in low or "coding agent" in low):
            self.pending[session_id] = {"stage": "provider"}
            self._dirty_sessions.add(session_id)
            self._save()
            return "Choose Codex, Claude, or agy. You can also name a model and effort."
        if selection_request:
            choice = normalize_selection(spoken)
            selected = self.selections.setdefault(session_id, {"provider": "codex", "model": None, "effort": "medium"})
            selected.update({key: value for key, value in choice.items() if value is not None})
            self._dirty_sessions.add(session_id)
            self._save()
            return self._selection_reply(selected)
        # Intercept an explicit VS Code task. Casual "Claude/Codex" discussion reaches the LLM.
        verb = r"(?:do|fix|build|implement|refactor|write|test|review|change|create|add|debug|update|make|finish|clean up|sort out)"
        mentions_vscode = re.search(r"\bvs\s?code\b", low)
        task_match = (
            re.search(rf"\b{verb}\b.+\b(?:in|on|with|inside|within)\s+(?:the\s+|my\s+|this\s+)?(?:vs\s?code|vscode|code editor|editor)\b", low)
            or (mentions_vscode and re.search(rf"\b{verb}\b", low))
        )
        if task_match:
            from . import coding
            context = coding.active_context()
            workspace = context.get("folder")
            if not workspace:
                return "I couldn't find an open VS Code workspace, sir. Open the project and try again."

            wants_reset = bool(re.search(r"\b(ask me again|different (agent|provider|model)|change the (agent|provider|model|effort)|reconfigure)\b", low))
            remembered = None if wants_reset else self.ws_selections.get(str(workspace))
            if wants_reset:
                self.ws_selections.pop(str(workspace), None)

            if remembered:
                # Same folder as before → skip the dialogue, just run it.
                try:
                    job = await self.start(spoken, workspace, remembered.get("provider") or "codex",
                                           remembered.get("model"), remembered.get("effort"))
                except Exception as exc:
                    return f"I couldn't start that coding job: {exc}"
                self._dirty_sessions.add(session_id)
                self._save()
                bits = job["provider"].title()
                if remembered.get("model"):
                    bits += f" ({remembered['model']})"
                if remembered.get("effort"):
                    bits += f", {remembered['effort']} effort"
                return f"Prompt given, sir. {bits} on {Path(workspace).name}, job {job['id']}."

            self.pending[session_id] = {
                "stage": "provider", "task": spoken, "workspace": workspace,
                "context": context, "snapshot": workspace_snapshot(workspace), "created_at": _now(),
            }
            self._dirty_sessions.add(session_id)
            self._save()
            return f"New folder ({Path(workspace).name}). Which coding provider: Codex, Claude, or agy?"
        return None

    def selection(self, session_id: str = "local") -> dict[str, str | None]:
        return dict(self.selections.get(session_id, {"provider": "codex", "model": None, "effort": "medium"}))

    @staticmethod
    def _selection_reply(selection: dict[str, str | None]) -> str:
        bits = [str(selection.get("provider") or "codex").title()]
        if selection.get("model"):
            bits.append(f"model {selection['model']}")
        if selection.get("effort"):
            bits.append(f"{selection['effort']} effort")
        return "Coding agent set to " + ", ".join(bits) + "."

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self.refresh()
        job = self.jobs.get(job_id)
        return asdict(job) if job else None

    def list_jobs(self, limit: int = 25) -> list[dict[str, Any]]:
        self.refresh()
        jobs = sorted(self.jobs.values(), key=lambda item: item.updated_at, reverse=True)[:max(0, limit)]
        return [asdict(job) for job in jobs]

    async def start(self, prompt: str, workspace: str, provider: str = "codex", model: str | None = None,
                    effort: str | None = None, timeout_s: int = 900) -> dict[str, Any]:
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise ValueError(f"Workspace does not exist: {workspace_path}")
        provider = (provider or "").strip().lower()
        effort = (effort or "").strip().lower() or None
        if not isinstance(timeout_s, int) or timeout_s <= 0:
            raise ValueError("timeout_s must be a positive integer")
        command = build_command(provider, prompt, str(workspace_path), model, effort)
        job = CodingJob(id=f"code-{uuid.uuid4().hex[:10]}", prompt=prompt, workspace=str(workspace_path),
                        provider=provider, model=model, effort=effort, timeout_s=timeout_s, command=command, before=workspace_snapshot(workspace_path))
        self.jobs[job.id] = job
        self._event(job, "queued", "Local CLI job queued")
        self._save()
        if self.runner is _run_process:
            # A detached Python worker owns the provider process and writes final state itself.
            # It is therefore independent of a FastAPI/voice backend restart.
            try:
                worker = subprocess.Popen([sys.executable, "-m", "jarvis.integrations.coding_jobs", "--worker",
                                           str(self.state_path), job.id], cwd=str(workspace_path), env=_clean_env(),
                                          start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                job.status, job.error = "failed", str(exc)
                self._event(job, "failed", str(exc))
                self._save()
                raise RuntimeError(str(exc)) from exc
            job.worker_pid = worker.pid
            self._event(job, "queued", f"Detached worker pid {worker.pid}")
            self._save()
            # The worker marks running only after it has created the real provider process.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                self.refresh()
                observed = self.jobs[job.id]
                if observed.status == "running" and observed.pid:
                    return asdict(observed)
                if observed.status == "failed":
                    raise RuntimeError(observed.error or "Provider CLI could not start")
            raise RuntimeError("Provider launch did not confirm within 5 seconds; job remains queued for status polling")
        else:
            self._tasks[job.id] = asyncio.create_task(self._execute(job.id))
        return asdict(job)

    async def _execute(self, job_id: str) -> None:
        job = self.jobs[job_id]
        try:
            if self.runner is _run_process:
                code, output, error = await self._run_default(job)
            else:
                job.status, job.started_at = "running", _now()
                self._event(job, "started", "Launching local CLI")
                self._save()
                code, output, error = await asyncio.wait_for(self.runner(job.command, job.workspace), timeout=job.timeout_s)
            job.output, job.error = output[-12000:], error[-12000:]
            job.status = "completed" if code == 0 else "failed"
            self._event(job, job.status, f"Exit code {code}")
        except Exception as exc:  # runner errors must be durable too
            job.status, job.error = "failed", str(exc)
            self._event(job, "failed", str(exc))
        finally:
            job.after, job.finished_at = workspace_snapshot(job.workspace), _now()
            job.summary = summarize(job)
            job.updated_at = job.finished_at
            self._event(job, job.status, job.summary[:200])
            self._save()
            self._tasks.pop(job_id, None)

    async def _run_default(self, job: CodingJob) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(*job.command, cwd=job.workspace, env=_clean_env(),
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise RuntimeError(f"Provider CLI could not start: {exc}") from exc
        job.pid, job.status, job.started_at = proc.pid, "running", _now()
        self._event(job, "started", f"Provider CLI pid {proc.pid}")
        self._save()
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=job.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "", f"Coding job timed out after {job.timeout_s} seconds."
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    def create_external(self, prompt: str, workspace: str, provider: str, model: str | None = None,
                        effort: str | None = None, external_id: str | None = None) -> dict[str, Any]:
        """Register a job owned by a provider/remote launcher. Follow with ``record_event``."""
        if provider not in PROVIDERS:
            raise ValueError(f"Unsupported coding provider: {provider}")
        job = CodingJob(id=external_id or f"external-{uuid.uuid4().hex[:10]}", prompt=prompt, workspace=str(Path(workspace).expanduser()),
                        provider=provider, model=model, effort=effort, status="running", external=True,
                        before=workspace_snapshot(workspace))
        self.jobs[job.id] = job
        self._event(job, "registered", "External provider job registered")
        self._save()
        return asdict(job)

    def record_event(self, job_id: str, status: str | None = None, message: str = "", output: str | None = None) -> dict[str, Any]:
        """Opt-in status hook for remote/background providers that Jarvis cannot inspect directly."""
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        if status:
            if status not in {"queued", "running", "unknown", *TERMINAL}:
                raise ValueError(f"Unsupported job status: {status}")
            job.status = status
            if status == "running" and not job.started_at:
                job.started_at = _now()
            if status in TERMINAL:
                job.finished_at, job.after = _now(), workspace_snapshot(job.workspace)
        if output is not None:
            job.output = output[-12000:]
        if job.status in TERMINAL:
            job.summary = summarize(job)
        self._event(job, "external", message or job.summary or (status or "update"))
        self._save()
        return asdict(job)

    def sync_external(self) -> list[dict[str, Any]]:
        """Reconcile jobs with the live provider processes on this machine.

        Registers a user-started ``codex``/``claude``/``agy`` run as an external job and marks a
        tracked external job completed once its process is gone. Returns the changed job dicts.
        """
        self.refresh()
        managed = {j.pid for j in self.jobs.values() if j.pid and j.status not in TERMINAL and not j.external}
        managed |= {j.worker_pid for j in self.jobs.values() if j.worker_pid and j.status not in TERMINAL}
        ext_by_pid = {j.pid: j for j in self.jobs.values() if j.external and j.pid}
        changed: list[dict[str, Any]] = []
        live_pids: set[int] = set()
        for proc in scan_provider_processes():
            pid = proc["pid"]
            live_pids.add(pid)
            if pid in managed or pid in ext_by_pid:
                continue
            job = CodingJob(
                id=f"ext-{proc['provider']}-{pid}", prompt=f"External {proc['provider']} session",
                workspace=proc["workspace"] or str(Path.home()), provider=proc["provider"],
                status="running", external=True, pid=pid, started_at=_now(),
                before=workspace_snapshot(proc["workspace"] or str(Path.home())),
            )
            self.jobs[job.id] = job
            self._event(job, "registered", f"Detected external {proc['provider']} (pid {pid})")
            changed.append(asdict(job))
        for pid, job in ext_by_pid.items():
            if pid not in live_pids and job.status == "running":
                job.finished_at, job.after = _now(), workspace_snapshot(job.workspace)
                job.status, job.summary = "completed", summarize(job)
                self._event(job, "completed", "External process exited")
                changed.append(asdict(job))
        if changed:
            self._save()
        return changed


# Convenience API for the command router and web process.  Tests and alternate processes can
# construct ``CodingJobManager(state_path=...)`` directly to keep their state isolated.
_DEFAULT_MANAGER: CodingJobManager | None = None


def get_manager() -> CodingJobManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = CodingJobManager()
    return _DEFAULT_MANAGER


async def handle_message(text: str, session_id: str = "local") -> str | None:
    return await get_manager().handle_message(text, session_id)


def list_jobs(limit: int = 25) -> list[dict[str, Any]]:
    return get_manager().list_jobs(limit)


def sync_external() -> list[dict[str, Any]]:
    return get_manager().sync_external()


def subscribe(callback: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
    return get_manager().subscribe(callback)


async def _worker(state_path: str, job_id: str) -> int:
    manager = CodingJobManager(state_path)
    if job_id not in manager.jobs:
        return 2
    await manager._execute(job_id)
    return 0


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Jarvis coding job worker")
    parser.add_argument("--worker", nargs=2, metavar=("STATE_FILE", "JOB_ID"))
    args = parser.parse_args()
    if args.worker:
        raise SystemExit(asyncio.run(_worker(*args.worker)))
    parser.print_help()


if __name__ == "__main__":
    _main()
