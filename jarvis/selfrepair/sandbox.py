"""Where a repair happens: its own git worktree, and a wall around every command it runs.

* The worktree is created from the running checkout's current commit, on a branch named
  ``jarvis/repair/<job-id>-<slug>``, in a private directory *outside* the repository
  (``~/.local/state/jarvis/repairs``). The running checkout is never edited.
* Paths are checked twice: lexically (inside the worktree, no ``..``) and after resolving
  symlinks, so a symlink planted in the worktree cannot point an edit or a read outside it.
* Commands are exact templates from ``policy.allowed_command``, run through the shell sandbox
  (``jarvis.agent.sandbox``) with only the worktree writable, the venv readable, an empty home —
  so no ``.env``, no credentials, no browser profiles — and no network; plus CPU, memory and
  file-size limits and a wall-clock timeout. Only a bounded tail of output is kept, and only
  long enough to count results; it is never stored or logged.
* Git goes through :func:`git`, which only runs subcommands ``policy.allowed_git`` lists. There
  is no push, no reset, no branch deletion, no remote.
"""
from __future__ import annotations

import os
import re
import resource
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import policy


class PolicyViolation(RuntimeError):
    """A repair tried to step outside what policy.py allows. Never caught to carry on."""


# ----------------------------------------------------------------------------- git
def git(args: list[str], cwd: Path, timeout: float = 60) -> subprocess.CompletedProcess:
    if not policy.allowed_git(args):
        raise PolicyViolation(f"git {args[0] if args else ''} is not permitted for repairs")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SSH_"))}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"})
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout, env=env, check=False)


def repo_root(start: Path) -> Path:
    """The top of the checkout `start` is in — and it has to be this project."""
    out = git(["rev-parse", "--show-toplevel"], start)
    if out.returncode != 0:
        raise PolicyViolation("not inside a git checkout")
    root = Path(out.stdout.strip()).resolve()
    if not (root / "jarvis" / "selfrepair" / "policy.py").is_file():
        raise PolicyViolation(f"{root} is not the Jarvis repository")
    return root


def head(cwd: Path) -> str:
    out = git(["rev-parse", "HEAD"], cwd)
    if out.returncode != 0:
        raise PolicyViolation("could not read HEAD")
    return out.stdout.strip()


_SLUG = re.compile(r"[^a-z0-9]+")


def branch_name(job_id: str, summary: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9a-f]{6}", job_id):
        raise PolicyViolation("malformed job id")
    slug = _SLUG.sub("-", summary.lower()).strip("-")[:32].strip("-") or "repair"
    return f"jarvis/repair/{job_id}-{slug}"


@dataclass
class Worktree:
    path: Path
    branch: str
    base: str


def create_worktree(live_repo: Path, job_id: str, summary: str,
                    root: Optional[Path] = None) -> Worktree:
    """A fresh worktree at the live checkout's HEAD, outside the repository."""
    repo = repo_root(live_repo)
    base = head(repo)
    root = (root or policy.repairs_root()).resolve()
    if root == repo or repo in root.parents:
        raise PolicyViolation("repair worktrees must live outside the repository")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / job_id
    if path.exists():
        raise PolicyViolation(f"worktree {path} already exists")
    branch = branch_name(job_id, summary)
    out = git(["worktree", "add", "-b", branch, str(path), base], repo, timeout=120)
    if out.returncode != 0:
        raise PolicyViolation(f"could not create the worktree: {out.stderr.strip()[:160]}")
    return Worktree(path.resolve(), branch, base)


def remove_worktree(live_repo: Path, path: Path) -> bool:
    """Remove a repair's own worktree. The branch is kept — branches are never deleted."""
    root = policy.repairs_root().resolve()
    path = Path(path).resolve()
    if root not in path.parents:
        raise PolicyViolation("refusing to remove a worktree outside the repairs directory")
    out = git(["worktree", "remove", "--force", str(path)], repo_root(live_repo), timeout=120)
    return out.returncode == 0


# ----------------------------------------------------------------------------- paths
def safe_path(worktree: Path, relative: str) -> Path:
    """`relative` inside `worktree`, or PolicyViolation — lexically and after symlinks."""
    if not relative or "\x00" in relative:
        raise PolicyViolation("empty path")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise PolicyViolation(f"{relative!r} leaves the worktree")
    base = Path(worktree).resolve()
    target = base / rel
    # Every existing component is checked, so a symlinked directory cannot redirect the file.
    probe = base
    for part in rel.parts:
        probe = probe / part
        if probe.is_symlink():
            raise PolicyViolation(f"{relative!r} goes through a symlink")
    resolved = target.resolve()
    if resolved != base and base not in resolved.parents:
        raise PolicyViolation(f"{relative!r} resolves outside the worktree")
    return target


def check_changed_paths(worktree: Path, paths: list[str]) -> None:
    """Every changed path must stay inside the worktree, be a regular file, and not be a symlink."""
    for rel in paths:
        target = safe_path(worktree, rel)
        if target.exists() and not target.is_file():
            raise PolicyViolation(f"{rel!r} is not a regular file")


# ----------------------------------------------------------------------------- commands
@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    tail: str                 # at most LIMITS.output_bytes, for counting results only
    duration_s: float
    timed_out: bool = False
    sandboxed: bool = False
    cancelled: bool = False


def _limits(limits: policy.Limits) -> Callable[[], None]:
    def apply() -> None:
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_s, limits.cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def scratch_dir(worktree: Path) -> Path:
    """Per-job scratch beside the worktree — never inside it, so it can never be committed."""
    return Path(worktree).resolve().parent / f"{Path(worktree).name}.scratch"


def _env(scratch: Path) -> dict[str, str]:
    # A clean environment: nothing from the parent's secrets, and markers that keep Jarvis's
    # own code from reaching out (no settings events, no away mode, no model calls).
    keep = ("PATH", "LANG", "LC_ALL", "TZ")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({
        # An empty home: no ~/.config/jarvis/.env, no credentials — even where bubblewrap is absent.
        "HOME": str(scratch / "home"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
        "JARVIS_STATE_DIR": str(scratch / "state"), "XDG_RUNTIME_DIR": str(scratch / "run"),
        "JARVIS_SETTINGS_EMIT": "0", "JARVIS_SELF_REPAIR_SANDBOXED": "1", "JARVIS_UPDATE_FROM": "none",
        "NO_PROXY": "*", "no_proxy": "*",
        # The GPU is shared with the running assistant; tests in a repair never compete for it.
        "CUDA_VISIBLE_DEVICES": "", "JARVIS_STT_DEVICE": "cpu",
    })
    return env


def run(argv: list[str], worktree: Path, *, timeout: float, limits: policy.Limits = policy.LIMITS,
        python: Optional[str] = None, cancelled: Callable[[], bool] = lambda: False,
        use_bwrap: Optional[bool] = None, scratch: Optional[Path] = None,
        readonly_tree: bool = False) -> CommandResult:
    """Run one allowed command inside the worktree, walled in. PolicyViolation if not allowed.

    ``readonly_tree`` mounts the tree read-only — used to probe the live checkout after an
    activation, with ``scratch`` somewhere outside it."""
    python = python or policy.venv_python()
    if not policy.allowed_command(argv, python):
        raise PolicyViolation(f"command not allowed: {' '.join(argv)[:120]}")
    worktree = Path(worktree).resolve()
    scratch = Path(scratch).resolve() if scratch else scratch_dir(worktree)
    for d in ("home", "state", "run"):
        (scratch / d).mkdir(parents=True, exist_ok=True, mode=0o700)
    from ..agent import sandbox as shell_sandbox

    if use_bwrap is None:
        # By default the wall is required, not hoped for: bubblewrap installed but unable to
        # start (no user namespaces, already inside a sandbox) must not quietly mean "run with
        # the network and the real home". Only an explicit False — tests, or the owner's
        # JARVIS_REPAIR_ALLOW_UNSANDBOXED=1 — runs without it.
        if not bwrap_works():
            if os.environ.get("JARVIS_REPAIR_ALLOW_UNSANDBOXED") != "1":
                raise PolicyViolation("no working sandbox (bubblewrap) is available for repair commands")
            use_bwrap = False
        else:
            use_bwrap = True
    sandboxed = use_bwrap
    command = argv
    if sandboxed:
        venv = str(Path(python).parent.parent)
        tree_rw = () if readonly_tree else (str(worktree),)
        tree_ro = (str(worktree),) if readonly_tree else ()
        wall = shell_sandbox.Policy(name="repair", writable=(*tree_rw, str(scratch)),
                                    readable=(venv, str(Path(os.path.realpath(python)).parent.parent), *tree_ro),
                                    network=False, home=False)
        import shlex

        command = shell_sandbox.build_argv(" ".join(shlex.quote(a) for a in argv), wall, str(worktree))
    started = time.monotonic()
    out_path = scratch / f"out-{os.getpid()}-{int(started * 1000)}.log"
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(command, cwd=str(worktree), stdout=out, stderr=subprocess.STDOUT,
                                env=_env(scratch), preexec_fn=_limits(limits))
        timed_out = was_cancelled = False
        while proc.poll() is None:
            if time.monotonic() - started > timeout:
                timed_out = True
            elif cancelled():
                was_cancelled = True
            if timed_out or was_cancelled:
                _kill_group(proc)
                break
            time.sleep(0.2)
        proc.wait()
    try:
        with open(out_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limits.output_bytes))
            tail = fh.read().decode("utf-8", "replace")
    finally:
        out_path.unlink(missing_ok=True)
    return CommandResult(argv, proc.returncode, tail, time.monotonic() - started,
                         timed_out, sandboxed, was_cancelled)


_BWRAP_WORKS: dict[str, bool] = {}


def bwrap_works() -> bool:
    """Whether bubblewrap can actually build a namespace here — installed is not enough."""
    if "ok" not in _BWRAP_WORKS:
        from ..agent import sandbox as shell_sandbox

        ok = False
        if shell_sandbox.available():
            try:
                argv = shell_sandbox.build_argv("true", shell_sandbox.STRICT)
                ok = subprocess.run(argv, capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        _BWRAP_WORKS["ok"] = ok
    return _BWRAP_WORKS["ok"]


def _kill_group(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


_SUMMARY = re.compile(r"(?:(\d+) failed)|(?:(\d+) passed)|(?:(\d+) errors?)")
_FAILING = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)


def pytest_counts(tail: str) -> tuple[int, int, int, list[str]]:
    """(passed, failed, errors, failing ids) from pytest's -q summary."""
    passed = failed = errors = 0
    lines = [line for line in tail.splitlines() if re.search(r"\b(passed|failed|error)", line)]
    if lines:
        for m in _SUMMARY.finditer(lines[-1]):
            failed += int(m.group(1) or 0)
            passed += int(m.group(2) or 0)
            errors += int(m.group(3) or 0)
    # Every id, not a sample: the regression gate compares these sets, and a new failure that
    # sorted after a cut-off would otherwise pass unseen. The job stores only the first few.
    ids = list(dict.fromkeys(re.sub(r"\x1b\[[0-9;]*m", "", i) for i in _FAILING.findall(tail)))
    return passed, failed, errors, ids
