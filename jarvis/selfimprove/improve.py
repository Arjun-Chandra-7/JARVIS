"""Have Claude fix a defect Jarvis keeps hitting, and prove the fix before keeping it.

The journal already holds the hard part of a bug report: what was asked for, what Jarvis did
instead, and which component it happened in — written at the moment it failed, with the real
inputs. This turns a recurring one of those into a branch.

What it deliberately does not do is merge. A programme that edits itself and then runs the result
unreviewed is a different and much worse thing than one that proposes a change: a single bad edit
to the command layer would be executing on this machine within seconds, and the thing best placed
to notice would be the thing that made the mistake. So the work happens in a git worktree on its
own branch, the whole suite must pass, no test may be deleted to make it pass, and the branch is
pushed for a person to look at. Main is never written to here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .journal import Failure

REPO = Path(__file__).resolve().parents[2]
# Opus 5 at medium effort: this is a bounded, well-described bug fix, not open-ended design.
MODEL = os.environ.get("JARVIS_IMPROVE_MODEL", "opus")
EFFORT = os.environ.get("JARVIS_IMPROVE_EFFORT", "medium")
TIMEOUT_S = int(os.environ.get("JARVIS_IMPROVE_TIMEOUT", "900"))


@dataclass
class Attempt:
    branch: str
    ok: bool
    summary: str
    tests: str = ""
    pushed: bool = False


def _git(*args: str, cwd: Optional[Path] = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd or REPO), capture_output=True,
                          text=True, timeout=timeout)


def _brief(failure: Failure, times: int) -> str:
    return f"""You are fixing a defect in JARVIS, a voice assistant, in this repository.

It has failed this way {times} times. This is a real recorded failure, not a hypothetical:

  what was asked   : {failure.request}
  what happened    : {failure.detail}
  where            : {failure.where or "unknown"}
  kind             : {failure.kind}

Find the cause and fix it. Requirements, in order of importance:

1. Fix the cause, not the symptom. If you cannot find the cause, change nothing and say so.
2. Add a test that fails before your change and passes after it.
3. Do not delete or weaken any existing test. The whole suite must still pass:
       .venv/bin/python -m pytest tests/ -q
4. Match the surrounding style. Comments explain why, never what.
5. Keep the change small and reviewable.

Do not commit, do not push, do not run git. Just edit the files.
Finish with one short paragraph saying what you changed and why."""


def _worktree(branch: str) -> Path:
    path = REPO / ".claude" / "worktrees" / branch
    _git("worktree", "add", "-b", branch, str(path), "main")
    return path


def _tests_pass(where: Path) -> tuple[bool, str]:
    try:
        run = subprocess.run([str(REPO / ".venv" / "bin" / "python"), "-m", "pytest", "tests/", "-q"],
                             cwd=str(where), capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "the test suite timed out"
    tail = (run.stdout or run.stderr).strip().splitlines()
    return run.returncode == 0, tail[-1] if tail else "no output"


def _tests_were_removed(where: Path) -> bool:
    """A suite that passes because the failing test is gone has proved nothing."""
    diff = _git("diff", "main", "--numstat", "--", "tests/", cwd=where).stdout
    for line in diff.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1].isdigit() and parts[0].isdigit():
            added, removed = int(parts[0]), int(parts[1])
            if removed > 4 and removed > added:
                return True
    return False


def attempt(failure: Failure, times: int = 2, push: bool = True) -> Attempt:
    """Try to fix one recurring failure. Returns what happened; raises nothing."""
    branch = "fix/" + (re.sub(r"[^a-z0-9]+", "-", f"{failure.kind}-{failure.where}".lower())
                       .strip("-")[:40] or "defect") + time.strftime("-%m%d%H%M")
    where: Optional[Path] = None
    try:
        where = _worktree(branch)
    except Exception as exc:  # noqa: BLE001
        return Attempt(branch, False, f"could not make a worktree: {exc}")

    try:
        run = subprocess.run(
            ["claude", "-p", _brief(failure, times),
             "--model", MODEL, "--effort", EFFORT,
             "--permission-mode", "acceptEdits"],
            cwd=str(where), capture_output=True, text=True, timeout=TIMEOUT_S)
        said = (run.stdout or run.stderr).strip()
    except subprocess.TimeoutExpired:
        return Attempt(branch, False, "Claude did not finish in time")
    except FileNotFoundError:
        return Attempt(branch, False, "the claude CLI is not installed")

    changed = _git("status", "--porcelain", cwd=where).stdout.strip()
    if not changed:
        _git("worktree", "remove", "--force", str(where))
        _git("branch", "-D", branch)
        return Attempt(branch, False, f"no change was made. {said[-300:]}")

    if _tests_were_removed(where):
        return Attempt(branch, False, "tests were deleted to make the suite pass; not keeping this")

    ok, tail = _tests_pass(where)
    if not ok:
        return Attempt(branch, False, f"the fix does not pass the suite: {tail}", tests=tail)

    _git("add", "-A", cwd=where)
    _git("commit", "-m",
         f"Fix: {failure.kind} in {failure.where or 'jarvis'}\n\n"
         f"Seen {times} times. Asked: {failure.request[:120]}\n"
         f"Happened: {failure.detail[:200]}\n\n"
         f"{said[-800:]}\n\nProposed by Jarvis; not reviewed by a person.",
         cwd=where)

    pushed = False
    if push:
        pushed = _git("push", "-u", "origin", branch, cwd=where, timeout=180).returncode == 0
    return Attempt(branch, True, said[-400:] or "fixed", tests=tail, pushed=pushed)
