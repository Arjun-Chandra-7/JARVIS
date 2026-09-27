"""Putting a verified repair live — and taking it back out if the live system disagrees.

    1. take the activation lock (one activation at a time, whatever process asks)
    2. record what is running: the live checkout's commit, and that it has no local edits
    3. apply the candidate with ``git merge --ff-only`` — only possible when the live checkout
       is still exactly where the repair started from, so nothing else is ever overwritten
    4. restart only the services that load the changed component (and only allowed ones)
    5. health: each unit active, each process reporting the new commit, no new tracebacks
    6. probe: the repair's reproduction test, run against the live checkout mounted read-only
    7. any failure → ``git revert`` of the repair commit (new history, nothing rewritten),
       restart again, and check that the rollback itself brought health back

Services and health are objects so a test (or a demonstration) can use stand-ins for systemd
and the HTTP endpoints; the git steps are always real.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

from . import policy
from .sandbox import PolicyViolation, git, head, repo_root


class ActivationBusy(RuntimeError):
    pass


@contextmanager
def activation_lock(root: Optional[Path] = None) -> Iterator[None]:
    root = root or policy.repairs_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(root / "activation.lock", "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ActivationBusy("another repair is being activated") from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ----------------------------------------------------------------------------- services
class Services:
    def restart(self, units: list[str]) -> dict[str, bool]:
        raise NotImplementedError

    def is_active(self, unit: str) -> bool:
        raise NotImplementedError


class SystemdServices(Services):
    def restart(self, units: list[str]) -> dict[str, bool]:
        results = {}
        for unit in units:
            if unit not in policy.SERVICES_ALLOWED:
                raise PolicyViolation(f"{unit} may not be restarted by a repair")
            out = subprocess.run(["systemctl", "--user", "restart", f"{unit}.service"],
                                 capture_output=True, text=True, timeout=90, check=False)
            results[unit] = out.returncode == 0
        return results

    def is_active(self, unit: str) -> bool:
        out = subprocess.run(["systemctl", "--user", "is-active", f"{unit}.service"],
                             capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.strip() == "active"


# ----------------------------------------------------------------------------- health
@dataclass
class Health:
    ok: bool
    checks: list[str] = field(default_factory=list)      # short, content-free lines


class HealthChecker:
    def check(self, units: list[str], commit: str, since: float) -> Health:
        raise NotImplementedError


class SystemHealth(HealthChecker):
    """The real thing: units active, each process on `commit`, no tracebacks since `since`."""

    def __init__(self, services: Services, wait_s: float = 90.0) -> None:
        self.services = services
        self.wait_s = wait_s

    def _backend_commit(self) -> Optional[str]:
        port = os.environ.get("JARVIS_WEB_PORT", "8770")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                data = json.loads(r.read().decode())
            return ((data.get("build") or {}).get("commit") or "")[:12] or None
        except (OSError, ValueError):
            return None

    def _voice_commit(self, since: float) -> Optional[str]:
        from .. import build_info

        row = build_info.published("voice")
        if not row or float(row.get("started_at", 0)) < since:
            return None
        return str(row.get("commit", ""))[:12] or None

    def _tracebacks(self, unit: str, since: float) -> int:
        out = subprocess.run(["journalctl", "--user", "-u", f"{unit}.service", "--since", f"@{int(since)}",
                              "-o", "cat", "--no-pager"], capture_output=True, text=True, timeout=20, check=False)
        return out.stdout.count("Traceback (most recent call last)")

    def check(self, units: list[str], commit: str, since: float) -> Health:
        want = commit[:12]
        checks: list[str] = []
        deadline = time.monotonic() + self.wait_s
        pending = set(units)
        while pending and time.monotonic() < deadline:
            for unit in list(pending):
                if not self.services.is_active(unit):
                    continue
                got = self._backend_commit() if unit == "jarvis-backend" else \
                    self._voice_commit(since) if unit == "jarvis-voice" else want
                if got == want:
                    pending.discard(unit)
                    checks.append(f"{unit}: active on {want}")
            if pending:
                time.sleep(2)
        for unit in pending:
            checks.append(f"{unit}: not healthy on {want} within {int(self.wait_s)}s")
        # A process that starts and then falls over is not healthy: give it a moment, then look.
        if not pending:
            time.sleep(5)
        for unit in units:
            n = self._tracebacks(unit, since)
            if n:
                checks.append(f"{unit}: {n} traceback(s) since the restart")
            if not self.services.is_active(unit):
                checks.append(f"{unit}: stopped after starting")
        ok = not pending and not any("traceback" in c or "stopped" in c for c in checks)
        return Health(ok, checks)


# ----------------------------------------------------------------------------- activation
@dataclass
class ActivationResult:
    ok: bool
    before: str = ""
    applied: str = ""
    restarted: dict = field(default_factory=dict)
    health: list[str] = field(default_factory=list)
    probe: str = ""
    error: str = ""
    rolled_back: bool = False
    rollback_ok: Optional[bool] = None
    rollback_commit: str = ""
    rollback_health: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


Probe = Callable[[Path], tuple[bool, str]]


class Activator:
    def __init__(self, live_repo: Path, services: Optional[Services] = None,
                 health: Optional[HealthChecker] = None, lock_root: Optional[Path] = None) -> None:
        self.live_repo = Path(live_repo)
        self.services = services or SystemdServices()
        self.health = health or SystemHealth(self.services)
        self.lock_root = lock_root

    def _clean(self, repo: Path) -> bool:
        return git(["status", "--porcelain", "--untracked-files=no"], repo).stdout.strip() == ""

    def activate(self, candidate: str, base: str, units: list[str], probe: Optional[Probe] = None,
                 still_allowed: Callable[[], bool] = lambda: True) -> ActivationResult:
        units = [u for u in units if u in policy.SERVICES_ALLOWED]
        with activation_lock(self.lock_root):
            repo = repo_root(self.live_repo)
            before = head(repo)
            result = ActivationResult(False, before=before)
            if not still_allowed():
                result.error = "the repair rules changed while this job ran"
                return result
            if not self._clean(repo):
                result.error = "the running checkout has local changes, so I left it alone"
                return result
            if before != base:
                result.error = "the running version changed since the repair was prepared"
                return result
            ancestor = git(["merge-base", "--is-ancestor", before, candidate], repo)
            if ancestor.returncode != 0:
                result.error = "the candidate does not build on the running version"
                return result
            since = time.time()
            merged = git(["merge", "--ff-only", "-q", candidate], repo)
            if merged.returncode != 0:
                result.error = "the candidate could not be applied cleanly"
                return result
            result.applied = head(repo)
            try:
                result.restarted = self.services.restart(units) if units else {}
                health = self.health.check(units, result.applied, since) if units else \
                    Health(True, ["no service loads this component; nothing to restart"])
                result.health = health.checks
                ok = health.ok and all(result.restarted.values())
                if ok and probe is not None:
                    probed, detail = probe(repo)
                    result.probe = detail
                    ok = probed
                result.ok = ok
                if not ok:
                    result.error = result.error or "the live checks failed after applying it"
            except Exception as exc:  # noqa: BLE001 — anything unexpected is a failed activation
                result.error = f"activation failed ({type(exc).__name__})"
                result.ok = False
            if not result.ok:
                self._rollback(repo, candidate, units, result)
            return result

    def _rollback(self, repo: Path, candidate: str, units: list[str], result: ActivationResult) -> None:
        result.rolled_back = True
        since = time.time()
        reverted = git(["revert", "--no-edit", candidate], repo, timeout=120)
        if reverted.returncode != 0:
            result.rollback_ok = False
            result.rollback_health = ["git revert failed; the checkout still has the repair applied"]
            return
        result.rollback_commit = head(repo)
        try:
            restarted = self.services.restart(units) if units else {}
            health = self.health.check(units, result.rollback_commit, since) if units else Health(True, [])
            result.rollback_health = health.checks
            result.rollback_ok = health.ok and all(restarted.values())
        except Exception as exc:  # noqa: BLE001
            result.rollback_health = [f"restart after rollback failed ({type(exc).__name__})"]
            result.rollback_ok = False

    def undo(self, candidate: str, units: list[str]) -> ActivationResult:
        """"Undo your last repair": revert it on the live checkout, restart, check health."""
        units = [u for u in units if u in policy.SERVICES_ALLOWED]
        with activation_lock(self.lock_root):
            repo = repo_root(self.live_repo)
            result = ActivationResult(True, before=head(repo))
            if not self._clean(repo):
                result.ok = False
                result.error = "the running checkout has local changes, so I left it alone"
                return result
            if git(["merge-base", "--is-ancestor", candidate, result.before], repo).returncode != 0:
                result.ok = False
                result.error = "that repair isn't part of the running version any more"
                return result
            self._rollback(repo, candidate, units, result)
            result.ok = bool(result.rollback_ok)
            return result


def repro_probe(test_path: str, python: Optional[str] = None, use_bwrap: Optional[bool] = None) -> Probe:
    """Run the repair's reproduction test against the live checkout, read-only, in the sandbox."""
    from . import sandbox

    def probe(repo: Path) -> tuple[bool, str]:
        if not (Path(repo) / test_path).is_file():
            return False, "the reproduction test is missing from the live checkout"
        py = python or policy.venv_python()
        scratch = policy.repairs_root() / "probe.scratch"
        res = sandbox.run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", test_path], repo,
                          timeout=policy.LIMITS.focused_timeout_s, python=py, scratch=scratch,
                          readonly_tree=True, use_bwrap=use_bwrap)
        passed, failed, errors, _ = sandbox.pytest_counts(res.tail)
        ok = res.returncode == 0 and passed > 0 and not failed and not errors
        return ok, f"reproduction test on the live checkout: {passed} passed, {failed + errors} failed"
    return probe


_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def valid_commit(value: str) -> bool:
    return bool(_COMMIT.match(value or ""))
