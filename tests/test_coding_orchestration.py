"""Milestone B: coding-job summary, external detection, and the VS Code trigger."""
import asyncio
from pathlib import Path

import pytest

from jarvis.integrations import coding_jobs
from jarvis.integrations.coding_jobs import CodingJob, CodingJobManager, summarize


def test_summarize_completed_annotates_git_delta():
    job = CodingJob(id="j1", prompt="x", workspace="/w", provider="claude", status="completed",
                    output="building...\n\x1b[32mAll tests passed\x1b[0m\nDone.",
                    before={"status": ["?? a.py"]}, after={"status": ["?? a.py", " M b.py", " M c.py"]})
    text = summarize(job)
    assert "2 files changed" in text and "All tests passed" in text and "\x1b[" not in text


def test_summarize_failed_uses_last_error_line():
    job = CodingJob(id="j2", prompt="x", workspace="/w", provider="codex", status="failed",
                    output="", error="Traceback...\nRuntimeError: provider CLI not found")
    assert summarize(job).startswith("failed — ") and "RuntimeError" in summarize(job)


def test_start_populates_summary_on_completion(tmp_path):
    state = tmp_path / "s.json"
    ws = tmp_path

    async def fake_runner(command, cwd):
        return 0, "patch applied\nrefactor complete", ""

    async def run():
        mgr = CodingJobManager(state, runner=fake_runner)
        job = await mgr.start("do it", str(ws), "claude")
        await mgr._tasks[job["id"]]
        return job["id"]

    jid = asyncio.run(run())
    saved = CodingJobManager(state).get_job(jid)
    assert saved["status"] == "completed"
    assert "refactor complete" in saved["summary"]


def test_external_event_terminal_sets_summary(tmp_path):
    state = tmp_path / "s.json"
    mgr = CodingJobManager(state)
    mgr.create_external("review", str(tmp_path), "agy", external_id="x1")
    out = mgr.record_event("x1", "completed", output="reviewed 4 files, no issues")
    assert out["status"] == "completed"
    assert "reviewed 4 files" in out["summary"]


def test_sync_external_registers_then_completes(tmp_path, monkeypatch):
    state = tmp_path / "s.json"
    mgr = CodingJobManager(state)
    proc = {"pid": 999999, "provider": "claude", "workspace": str(tmp_path), "cmdline": "claude --print"}

    monkeypatch.setattr(coding_jobs, "scan_provider_processes", lambda: [proc])
    changed = mgr.sync_external()
    assert len(changed) == 1 and changed[0]["external"] and changed[0]["status"] == "running"
    job_id = changed[0]["id"]

    monkeypatch.setattr(coding_jobs, "scan_provider_processes", lambda: [])
    changed = mgr.sync_external()
    assert changed and changed[0]["id"] == job_id and changed[0]["status"] == "completed"
    assert CodingJobManager(state).get_job(job_id)["status"] == "completed"


@pytest.mark.parametrize("phrase", [
    "fix the authentication bug in the VS Code project I'm working on",
    "refactor the parser in vscode",
    "add a test in the VS Code editor",
    "debug the login flow in VS Code",
])
def test_a_vscode_task_starts_without_being_asked_anything(tmp_path, monkeypatch, phrase):
    """The whole point of the orchestration: work starts, nothing is asked.

    This used to answer with "Which coding provider: Codex, Claude, or agy?", then a question
    about the model, then one about effort — three turns before a line of code was written, and
    the answers were the ones the roster would have picked anyway.
    """
    async def fake_runner(command, cwd):
        return 0, "done", ""

    # It really launches now, so it needs somewhere harmless to launch into. Before this change
    # the request stopped at a question and no runner was ever reached.
    mgr = CodingJobManager(tmp_path / "s.json", runner=fake_runner)
    monkeypatch.setattr("jarvis.integrations.coding.active_context",
                        lambda: {"folder": str(tmp_path), "file": "x.py"})
    reply = asyncio.run(mgr.handle_message(phrase, "voice"))
    assert reply and "Prompt given" in reply
    assert "?" not in reply.rstrip("?") + ""      # nothing was asked back
    assert not mgr.pending                        # and nothing is waiting on an answer
    # It says what it chose, because an override has to be possible without a menu.
    assert mgr.selections["voice"]["provider"] in {"codex", "claude", "agy"}
    assert mgr.ws_selections[str(tmp_path)]["effort"] in {"low", "medium", "high", "xhigh", "max"}


def test_an_old_pending_cannot_swallow_the_next_thing_said(tmp_path, monkeypatch):
    """The state was saved to disk, so a half-finished dialogue outlived the restart that ended
    it — and every message afterwards came back "Choose Codex, Claude, or agy."
    """
    mgr = CodingJobManager(tmp_path / "s.json")
    mgr.pending["voice"] = {"stage": "provider", "task": "old", "workspace": str(tmp_path)}
    monkeypatch.setattr("jarvis.integrations.coding.active_context",
                        lambda: {"folder": str(tmp_path), "file": "x.py"})
    reply = asyncio.run(mgr.handle_message("what's the weather", "voice"))
    assert reply is None              # not a coding request, so it passes through
    assert not mgr.pending            # and the stale dialogue is gone


@pytest.mark.parametrize("phrase", [
    "what do you think of Claude versus Codex",
    "explain how vs code extensions work",
])
def test_casual_mention_is_not_intercepted(tmp_path, phrase):
    mgr = CodingJobManager(tmp_path / "s.json")
    assert asyncio.run(mgr.handle_message(phrase, "voice")) is None
