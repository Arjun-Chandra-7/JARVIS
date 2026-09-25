"""Who writes the change. Whatever it is, it only ever writes inside the repair's worktree, and
``checks.inspect`` judges the result as if it had been written by a stranger.

Two stages, called separately so the pipeline can run tests in between:

    write_repro(ctx)  a test that reproduces the reported failure *from the outside* — it must
                      fail on the unmodified code, or the job stops at "couldn't reproduce"
    write_fix(ctx)    the smallest change that makes it pass; the repro test is frozen by then

``ClaudeEditor`` is the existing coding agent (``coding_jobs.build_command("claude", …)``) with a
policy boundary around it: file tools only — no shell, no web — and, where bubblewrap exists,
run inside a namespace in which only the worktree and the agent's own config are writable and
the running checkout does not exist at all. It needs the network to reach its model; that is the
one outside connection a repair makes, and the tests it wrote run with no network.

``RecipeEditor`` applies a prepared change from a JSON file. It exists for fixtures and
demonstrations of the pipeline, and a job it ran says so in its evidence.
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import policy
from .sandbox import PolicyViolation, safe_path, scratch_dir


@dataclass
class EditContext:
    worktree: Path
    component: policy.Component
    summary: str
    evidence: list[str]
    repro_test: str                          # "tests/test_repair_<id>.py"
    timeout_s: int = policy.LIMITS.edit_timeout_s
    cancelled: Callable[[], bool] = lambda: False
    notes: list[str] = field(default_factory=list)


class Editor:
    name = "editor"

    def write_repro(self, ctx: EditContext) -> None:
        raise NotImplementedError

    def write_fix(self, ctx: EditContext) -> None:
        raise NotImplementedError


# ----------------------------------------------------------------------------- recipe
class RecipeEditor(Editor):
    """{"repro": {"tests/test_repair_x.py": "<source>"},
        "fix": [{"path": "jarvis/...", "old": "<exact text>", "new": "<replacement>"}]}"""

    name = "recipe"

    def __init__(self, recipe: dict) -> None:
        self.recipe = recipe

    @classmethod
    def from_file(cls, path: str | Path) -> "RecipeEditor":
        return cls(json.loads(Path(path).read_text()))

    def write_repro(self, ctx: EditContext) -> None:
        ctx.notes.append("change supplied by a prepared recipe, not written by the coding agent")
        for rel, source in (self.recipe.get("repro") or {}).items():
            if rel not in {"@repro", ctx.repro_test} and not rel.startswith("tests/test_repair_"):
                raise PolicyViolation(f"a reproduction may only add tests/test_repair_*.py, not {rel}")
            target = safe_path(ctx.worktree, ctx.repro_test if rel == "@repro" else rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)

    def write_fix(self, ctx: EditContext) -> None:
        for edit in self.recipe.get("fix") or []:
            target = safe_path(ctx.worktree, edit["path"])
            text = target.read_text()
            if text.count(edit["old"]) != 1:
                raise PolicyViolation(f"the recipe's anchor text is not unique in {edit['path']}")
            target.write_text(text.replace(edit["old"], edit["new"], 1))


# ----------------------------------------------------------------------------- claude
_BRIEF = """You are repairing one bug in JARVIS, a local voice assistant. You are in an isolated git
worktree; nothing you do here runs until it has been tested and checked.

Reported by the owner: {summary}
Component: {component} — {description}
Evidence gathered (exception types and locations only): {evidence}

Rules — a change that breaks any of them is discarded automatically:
* You may only edit these files: {files}
* You may add exactly one new test file: {repro}
* No new dependencies, no network calls, no changes to tests other than {repro}.
* Keep the change minimal: fix the cause, do not refactor.
"""

_REPRO_TASK = """Stage 1 of 2. Write {repro}: a pytest test that reproduces the reported failure as
the owner would see it (the wrong visible result, the lost utterance, the exception) — not a
test of implementation details. It must FAIL on the current code. Do not change any other
file in this stage. Use fakes/monkeypatch instead of real hardware, models or network."""

_FIX_TASK = """Stage 2 of 2. {repro} now fails as intended. Make the smallest change to the allowed
files so it passes, without editing {repro}."""


def _scrubbed_env() -> dict[str, str]:
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(word in upper for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "COOKIE", "SESSION")):
            continue
        if upper.startswith(("JARVIS_", "GROQ", "OPENAI", "GEMINI", "TELEGRAM", "GOOGLE")):
            continue
        env[key] = value
    env["JARVIS_SELF_REPAIR_SANDBOXED"] = "1"
    return env


class ClaudeEditor(Editor):
    name = "claude"
    TOOLS = "Read,Edit,Write,Glob,Grep"
    DENIED = "Bash,WebFetch,WebSearch,Task,NotebookEdit"

    def available(self) -> bool:
        return shutil.which("claude") is not None

    def _command(self, prompt: str, worktree: Path) -> list[str]:
        from ..integrations.coding_jobs import build_command

        cmd = build_command("claude", prompt, str(worktree), effort="medium")
        prompt_arg = cmd.pop()
        cmd += ["--allowedTools", self.TOOLS, "--disallowedTools", self.DENIED, prompt_arg]
        from ..agent import sandbox as shell_sandbox

        if not shell_sandbox.available():
            return cmd
        binary = shutil.which("claude") or "claude"
        home = Path.home()
        wall = shell_sandbox.Policy(
            name="repair-editor",
            writable=(str(worktree), str(scratch_dir(worktree)), str(home / ".claude"), str(home / ".claude.json")),
            readable=(str(Path(binary).parent), str(Path(os.path.realpath(binary)).parent.parent)),
            network=True, home=False)
        import shlex

        return shell_sandbox.build_argv(" ".join(shlex.quote(a) for a in cmd), wall, str(worktree))

    def _run(self, prompt: str, ctx: EditContext) -> None:
        if not self.available():
            raise PolicyViolation("no coding agent is installed")
        cmd = self._command(prompt, ctx.worktree)

        def limits() -> None:
            os.setsid()
            resource.setrlimit(resource.RLIMIT_FSIZE, (policy.LIMITS.file_bytes, policy.LIMITS.file_bytes))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        proc = subprocess.Popen(cmd, cwd=str(ctx.worktree), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=_scrubbed_env(), preexec_fn=limits)
        from .sandbox import _kill_group
        import time

        started = time.monotonic()
        while proc.poll() is None:
            if ctx.cancelled() or time.monotonic() - started > ctx.timeout_s:
                _kill_group(proc)
                raise PolicyViolation("the coding agent was stopped" if ctx.cancelled()
                                      else "the coding agent ran out of time")
            time.sleep(0.5)
        if proc.returncode != 0:
            raise PolicyViolation(f"the coding agent exited with status {proc.returncode}")

    def _brief(self, ctx: EditContext) -> str:
        return _BRIEF.format(summary=ctx.summary, component=ctx.component.name,
                             description=ctx.component.description or ctx.component.name,
                             evidence="; ".join(ctx.evidence) or "none recorded",
                             files=", ".join(ctx.component.files), repro=ctx.repro_test)

    def write_repro(self, ctx: EditContext) -> None:
        self._run(self._brief(ctx) + "\n" + _REPRO_TASK.format(repro=ctx.repro_test), ctx)

    def write_fix(self, ctx: EditContext) -> None:
        self._run(self._brief(ctx) + "\n" + _FIX_TASK.format(repro=ctx.repro_test), ctx)


def default_editor() -> Optional[Editor]:
    """JARVIS_REPAIR_EDITOR: "claude" (default), "recipe:<path.json>", or "none"."""
    choice = os.environ.get("JARVIS_REPAIR_EDITOR", "claude").strip()
    if choice.startswith("recipe:"):
        return RecipeEditor.from_file(choice.split(":", 1)[1])
    if choice == "none":
        return None
    editor = ClaudeEditor()
    return editor if editor.available() else None
