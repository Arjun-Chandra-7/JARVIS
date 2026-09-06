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
def test_vscode_task_is_intercepted(tmp_path, monkeypatch, phrase):
    mgr = CodingJobManager(tmp_path / "s.json")
    monkeypatch.setattr("jarvis.integrations.coding.active_context",
                        lambda: {"folder": str(tmp_path), "file": "x.py"})
    reply = asyncio.run(mgr.handle_message(phrase, "voice"))
    assert reply and ("provider" in reply.lower() or "codex" in reply.lower())
    assert mgr.pending["voice"]["stage"] == "provider"
    assert mgr.pending["voice"]["workspace"] == str(tmp_path)


@pytest.mark.parametrize("phrase", [
    "what do you think of Claude versus Codex",
    "explain how vs code extensions work",
])
def test_casual_mention_is_not_intercepted(tmp_path, phrase):
    mgr = CodingJobManager(tmp_path / "s.json")
    assert asyncio.run(mgr.handle_message(phrase, "voice")) is None
