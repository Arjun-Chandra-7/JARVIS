"""One repair, start to finish, as a sequence of checked states.

    gathering_evidence  failure journal + service logs for the component: exception types and
                        file:line only — never message text, transcripts or screen contents
    reproducing         a worktree at the running commit; a baseline of the regression suite;
                        the editor writes a reproduction test; it must FAIL here
    proposed → editing  the editor writes the fix; the reproduction test is frozen
    testing             diff boundary (checks.inspect), reproduction passes, focused tests pass,
                        static checks pass, the regression suite has no new failures
    commit              one commit on the repair branch; never pushed
    awaiting_activation tier 1: announce, then activate; tier 2: wait for a specific approval
    activating / health_checking / completed | rolled_back

Every step checks for cancellation and re-checks that the policy file is the one the job
started with. A failure anywhere ends the job with a sentence that says exactly what happened.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from . import checks, jobs, policy, sandbox
from .editor import EditContext, Editor
from .jobs import JobStore, RepairJob, TestRun

COULD_NOT_REPRODUCE = "I couldn't reproduce the issue, so I haven't changed anything."
TESTS_FAILED = "The change failed its tests, so I did not activate it."
NEEDS_APPROVAL = "The repair is ready, but activation needs your approval."
ROLLED_BACK = "Activation failed, so I restored the previous version."


class Cancelled(Exception):
    pass


class Stop(Exception):
    """End the job in FAILED with this sentence as the outcome."""


def _regression_mode() -> str:
    return os.environ.get("JARVIS_REPAIR_REGRESSION", "full").strip().lower()


class Pipeline:
    def __init__(self, store: JobStore, live_repo: Path, editor: Optional[Editor],
                 activator=None, notify: Callable[[RepairJob, str], None] = lambda job, text: None,
                 python: Optional[str] = None, worktree_root: Optional[Path] = None,
                 auto_activate: Optional[bool] = None, use_bwrap: Optional[bool] = None) -> None:
        self.store = store
        self.live_repo = Path(live_repo)
        self.editor = editor
        self.activator = activator
        self.notify = notify
        self.python = python or policy.venv_python()
        self.worktree_root = worktree_root
        self.use_bwrap = use_bwrap
        self.auto_activate = (os.environ.get("JARVIS_REPAIR_AUTO_ACTIVATE", "1") != "0"
                              if auto_activate is None else auto_activate)

    # ------------------------------------------------------------------ plumbing
    def _job(self, job_id: str) -> RepairJob:
        job = self.store.get(job_id)
        if job is None:
            raise Stop("that repair job no longer exists")
        return job

    def _guard(self, job: RepairJob) -> None:
        fresh = self._job(job.id)
        if fresh.cancel_requested:
            raise Cancelled()
        if policy.disabled():
            raise Stop("Self-repair was switched off while I was working, so I stopped.")
        if policy.digest() != job.policy_digest:
            raise Stop("The repair rules changed while this job was running, so I stopped it.")

    def _to(self, job: RepairJob, state: str, note: str = "", **fields) -> RepairJob:
        self._guard(job)
        job = self.store.transition(job.id, state, note, **fields)
        return job

    def _run(self, job: RepairJob, name: str, argv: list[str], timeout: float) -> TestRun:
        wt = Path(job.worktree)
        started = time.monotonic()
        res = sandbox.run(argv, wt, timeout=timeout, python=self.python,
                          cancelled=lambda: self._job(job.id).cancel_requested, use_bwrap=self.use_bwrap)
        if res.cancelled:
            raise Cancelled()
        passed, failed, errors, failing = sandbox.pytest_counts(res.tail) if "pytest" in argv else (0, 0, 0, [])
        run = TestRun(name=name, command=" ".join(a if not a.startswith("/") else Path(a).name for a in argv),
                      passed=passed, failed=failed, errors=errors, ok=res.returncode == 0 and not res.timed_out,
                      duration_s=round(time.monotonic() - started, 1), failing=failing)
        if res.timed_out:
            run.failing = ["timed out"]
        job = self._job(job.id)
        row = asdict(run)
        row["failing"] = row["failing"][:20]      # stored: a sample; compared: the full list
        self.store.update(job.id, tests=job.tests + [row])
        return run

    def _pytest(self, *targets: str, stop_first: bool = False) -> list[str]:
        return [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *(["-x"] if stop_first else []), *targets]

    def _focused_targets(self, job: RepairJob, component: policy.Component) -> list[str]:
        wt = Path(job.worktree)
        found = []
        for pattern in component.tests:
            for path in sorted(wt.glob(pattern)):
                rel = str(path.relative_to(wt))
                if rel not in found:
                    found.append(rel)
        return found

    # ------------------------------------------------------------------ stages
    def gather(self, job: RepairJob, component: policy.Component) -> list[str]:
        evidence: list[str] = []
        try:
            from ..selfimprove import journal

            # Kind and location only: the journal also holds what was asked and said, which is
            # the owner's words and never leaves the journal.
            for times, failure in journal.recurring(minimum=1)[:5]:
                where = getattr(failure, "where", "") or ""
                kind = getattr(failure, "kind", "") or "failure"
                evidence.append(f"{kind} seen {times}× {('at ' + where) if where else ''}".strip())
        except Exception:  # noqa: BLE001 — evidence is best effort
            pass
        for unit in component.services:
            evidence += _log_exceptions(unit)
        return evidence[:12]

    def run(self, job_id: str) -> RepairJob:
        job = self._job(job_id)
        component = policy.component_named(job.component)
        try:
            if policy.disabled():
                raise Stop("Self-repair is switched off (JARVIS_SELF_REPAIR_DISABLED), so I didn't change anything.")
            if component is None:
                raise Stop("I couldn't tell which part of me that is about, so I haven't changed anything.")
            if self.editor is None:
                raise Stop("There's no coding agent available to prepare a repair, so I've only recorded the report.")
            job = self._to(job, jobs.GATHERING)
            evidence = self.gather(job, component)
            job = self.store.update(job.id, evidence=evidence)

            job = self._to(job, jobs.REPRODUCING)
            wt = sandbox.create_worktree(self.live_repo, job.id, job.summary, self.worktree_root)
            job = self.store.update(job.id, worktree=str(wt.path), branch=wt.branch, base_commit=wt.base)

            baseline_failing: set[str] = set()
            baseline_count = 0
            if _regression_mode() == "full":
                base = self._run(job, "regression before", self._pytest("tests"), policy.LIMITS.test_timeout_s)
                baseline_failing = set(base.failing)
                baseline_count = base.failed + base.errors

            repro = f"tests/test_repair_{job.id.replace('-', '_')}.py"
            ctx = EditContext(wt.path, component, job.summary, job.evidence, repro,
                              cancelled=lambda: self._job(job.id).cancel_requested)
            self.editor.write_repro(ctx)
            self._guard(job)
            written = checks.changed_files(wt.path)
            if written != [repro] and not (written and all(p.startswith("tests/test_repair_") for p in written)):
                raise Stop("The reproduction step changed more than a test file, so I discarded it.")
            before = self._run(job, "focused before", self._pytest(*written), policy.LIMITS.focused_timeout_s)
            if before.ok or before.failed == 0:
                raise Stop(COULD_NOT_REPRODUCE)
            frozen = {p: checks.file_digest(wt.path / p) for p in written}
            job = self.store.update(job.id, evidence=job.evidence + ctx.notes)

            job = self._to(job, jobs.PROPOSED, f"reproduced: {before.failed} failing")
            if job.label == "diagnostic":
                # Asked why, not asked to fix: report the finding and change nothing.
                finding = (f"I reproduced it: the {component.name} check fails on the running version"
                           f" ({before.failed} failing). Say “fix it” and I'll prepare a repair.")
                job = self.store.transition(job.id, jobs.COMPLETED, "diagnosed", outcome=finding)
                self._cleanup(job)
                self.notify(job, finding)
                return job
            job = self._to(job, jobs.EDITING)
            self.editor.write_fix(ctx)
            self._guard(job)

            job = self._to(job, jobs.TESTING)
            report = checks.inspect(wt.path, component, frozen)
            job = self.store.update(job.id, changed_files=report.changed, diff_lines=report.lines,
                                    protected_areas=sorted(report.protected))
            if not report.ok:
                raise Stop("I discarded the change: " + "; ".join(report.violations) + ".")
            after = self._run(job, "focused after", self._pytest(*written), policy.LIMITS.focused_timeout_s)
            if not after.ok:
                raise Stop(TESTS_FAILED)
            focused = [t for t in self._focused_targets(job, component) if t not in written]
            if focused:
                if not self._run(job, "component tests", self._pytest(*focused), policy.LIMITS.test_timeout_s).ok:
                    raise Stop(TESTS_FAILED)
            py = [p for p in report.changed if p.endswith(".py")]
            if py and not self._run(job, "static", [self.python, "-m", "py_compile", *py], 120).ok:
                raise Stop(TESTS_FAILED)
            for js in [p for p in report.changed if p.endswith(".js")]:
                if not self._run(job, "static", ["node", "--check", js], 60).ok:
                    raise Stop(TESTS_FAILED)
            if _regression_mode() == "full":
                reg = self._run(job, "regression after", self._pytest("tests"), policy.LIMITS.test_timeout_s)
                after_failing = reg.failing
                new_failures = [f for f in after_failing if f not in baseline_failing]
                # Ids and counts both: a failure the summary did not name still shows in the count.
                if new_failures or reg.failed + reg.errors > baseline_count or (not reg.ok and not after_failing):
                    raise Stop(TESTS_FAILED)

            self._guard(job)
            message = f"Repair {job.id}: {job.summary}"[:72]
            sandbox.git(["add", "--", *report.changed], wt.path)
            commit = sandbox.git(["commit", "-q", "--no-verify", "-m", message], wt.path)
            if commit.returncode != 0:
                raise Stop("I couldn't commit the repair in its worktree, so nothing was activated.")
            candidate = sandbox.head(wt.path)

            tier = 2 if (component.protected or report.escalations) else 1
            reasons = ([f"{component.name} is protected ({component.protected})"] if component.protected else []) \
                + report.escalations
            areas = sorted(set(report.protected) | ({component.protected} if component.protected else set()))
            job = self._to(job, jobs.AWAITING, f"tier {tier}", candidate_commit=candidate, tier=tier,
                           tier_reasons=reasons, protected_areas=areas)
            if tier == 2 or not self.auto_activate or self.activator is None:
                why = f" It touches {', '.join(areas)}." if areas else (" " + reasons[0] + "." if reasons else "")
                job = self.store.update(job.id, outcome=f"{NEEDS_APPROVAL}{why}")
                self.notify(job, job.outcome)
                return job
            self.notify(job, f"I've fixed the {component.name} problem and it passed its tests. Applying it now.")
            return self.activate(job.id)
        except Cancelled:
            return self._finish(job_id, jobs.CANCELLED, "Cancelled. Nothing was activated, and the running version is untouched.")
        except Stop as stop:
            return self._finish(job_id, jobs.FAILED, str(stop))
        except sandbox.PolicyViolation as exc:
            return self._finish(job_id, jobs.FAILED, f"I stopped the repair: {exc}.")
        except Exception as exc:  # noqa: BLE001 — nothing unexpected may leave a job half-done
            return self._finish(job_id, jobs.FAILED, f"The repair stopped on an unexpected error ({type(exc).__name__}). Nothing was activated.")

    def activate(self, job_id: str, *, approved: bool = False) -> RepairJob:
        job = self._job(job_id)
        component = policy.component_named(job.component)
        if job.state != jobs.AWAITING or not job.candidate_commit or component is None:
            return job
        if job.tier >= 2 and not approved:
            return job
        if self.activator is None:
            return self.store.update(job.id, outcome=NEEDS_APPROVAL)
        try:
            job = self._to(job, jobs.ACTIVATING)
            from .activate import repro_probe

            repro = next((t for t in job.changed_files if t.startswith("tests/test_repair_")), "")
            probe = repro_probe(repro, self.python, self.use_bwrap) if repro else None
            job = self._to(job, jobs.HEALTH)
            result = self.activator.activate(job.candidate_commit, job.base_commit, list(component.services),
                                             probe=probe, still_allowed=lambda: policy.digest() == job.policy_digest)
            if result.ok:
                job = self.store.transition(job.id, jobs.COMPLETED, "activated", activation=result.as_dict(),
                                            outcome=_done_sentence(component))
                self._cleanup(job)
                self.notify(job, job.outcome)
                return job
            if result.rolled_back:
                sentence = ROLLED_BACK if result.rollback_ok else \
                    ("Activation failed, and the rollback didn't bring everything back healthy — "
                     "the running version needs a manual look (docs/SELF_REPAIR.md, manual recovery).")
                job = self.store.transition(job.id, jobs.ROLLED_BACK, result.error, activation=result.as_dict(),
                                            rollback={"ok": result.rollback_ok, "commit": result.rollback_commit,
                                                      "health": result.rollback_health}, outcome=sentence)
                self.notify(job, job.outcome)
                return job
            job = self.store.transition(job.id, jobs.FAILED, result.error, activation=result.as_dict(),
                                        outcome=f"I didn't activate the repair: {result.error}.")
            self.notify(job, job.outcome)
            return job
        except Cancelled:
            return self._finish(job_id, jobs.CANCELLED, "Cancelled before activation. The running version is untouched.")
        except Exception as exc:  # noqa: BLE001
            return self._finish(job_id, jobs.FAILED, f"Activation stopped on an unexpected error ({type(exc).__name__}).")

    def _finish(self, job_id: str, state: str, sentence: str) -> RepairJob:
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.terminal:
            try:
                job = self.store.transition(job_id, state, sentence[:120], outcome=sentence)
            except jobs.TransitionError:
                job = self.store.update(job_id, outcome=sentence)
        self._cleanup(job)
        self.notify(job, sentence)
        return job

    def _cleanup(self, job: RepairJob) -> None:
        """A finished job's worktree goes; its branch (and so its commit) stays for review."""
        if not job.worktree or not job.terminal:
            return
        try:
            sandbox.remove_worktree(self.live_repo, Path(job.worktree))
        except Exception:  # noqa: BLE001 — a leftover worktree is untidy, not unsafe
            pass
        scratch = sandbox.scratch_dir(Path(job.worktree))
        if scratch.exists() and policy.repairs_root().resolve() in scratch.resolve().parents:
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)


def _done_sentence(component: policy.Component) -> str:
    return f"Done, sir. I fixed the {component.name} problem, verified it against the failing case, and it's live."


_LOC = re.compile(r'File "[^"]*/(jarvis/[^"]+\.py)", line (\d+)')
_EXC = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Failure|Timeout))\b")


def _log_exceptions(unit: str, since: str = "-24h") -> list[str]:
    """Exception types and the innermost jarvis/ location from a unit's recent tracebacks."""
    if os.environ.get("JARVIS_SELF_REPAIR_SANDBOXED") or os.environ.get("PYTEST_CURRENT_TEST"):
        return []
    try:
        out = subprocess.run(["journalctl", "--user", "-u", f"{unit}.service", "--since", since, "-o", "cat",
                              "--no-pager"], capture_output=True, text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found: dict[str, int] = {}
    where = ""
    for line in out.splitlines()[-4000:]:
        loc = _LOC.search(line)
        if loc:
            where = f"{loc.group(1)}:{loc.group(2)}"
            continue
        exc = _EXC.match(line.strip())
        if exc and where:
            key = f"{exc.group(1).rsplit('.', 1)[-1]} at {where}"
            found[key] = found.get(key, 0) + 1
            where = ""
    return [f"{k} ({n}×)" for k, n in sorted(found.items(), key=lambda kv: -kv[1])[:5]]
