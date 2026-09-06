"""Milestone D: Meet lifecycle states, summary, and vault persistence."""
import asyncio
from types import SimpleNamespace

import pytest

from jarvis.integrations import meet_bot


@pytest.fixture(autouse=True)
def _reset():
    meet_bot._bot_task = None
    meet_bot._notes_path = None
    meet_bot._status = {"state": "idle", "detail": "", "url": "", "started_at": None,
                        "summary": "", "vault_note": None}
    yield
    meet_bot._bot_task = None


TRANSCRIPT = [
    "[10:00] -- Jarvis joined the meeting (observing) --",
    "[10:01] -- participant joined: Maya --",
    "[10:01] -- participant joined: Sam --",
    "[10:02] Maya: let's ship the parser on friday",
    "[10:03] Sam: I'll write the tests",
    "[10:20] -- disconnected from the meeting --",
]


def test_states_are_the_canonical_set():
    assert meet_bot.STATES == ("idle", "opening", "waiting_for_admission", "joined",
                               "recording_notes", "disconnected", "completed", "failed")


def test_set_state_rejects_unknown():
    meet_bot._set_state("banana", "x")
    assert meet_bot._status["state"] == "failed"
    meet_bot._set_state("recording_notes", "ok")
    assert meet_bot._status["state"] == "recording_notes"


def test_heuristic_summary_lists_participants_and_last_exchange():
    s = meet_bot._summarize_transcript(TRANSCRIPT)
    assert "Maya" in s and "Sam" in s
    assert "caption line(s) captured" in s
    assert "tests" in s


def test_summary_prefers_injected_llm():
    s = meet_bot._summarize_transcript(TRANSCRIPT, llm=lambda p: "Team agreed to ship Friday; Sam owns tests.")
    assert s == "Team agreed to ship Friday; Sam owns tests."


def test_save_vault_note_writes_jarvis_authored_markdown(tmp_path):
    config = SimpleNamespace(vault_path=tmp_path, user_name="sir", brain="chatgpt")
    path = meet_bot._save_vault_note(config, "https://meet.google.com/twa-pgjz-gss", TRANSCRIPT, "short summary")
    assert path and path.endswith(".md")
    text = (tmp_path / "Meetings").glob("*.md").__next__().read_text()
    assert "author: jarvis" in text and "## Summary" in text and "short summary" in text
    assert "## Transcript" in text and "Maya: let's ship the parser" in text


def test_join_meet_refuses_a_second_concurrent_meeting():
    meet_bot._bot_task = SimpleNamespace(done=lambda: False)
    meet_bot._status["state"] = "recording_notes"
    out = asyncio.run(meet_bot.join_meet("", "soon"))
    assert "Already in a meeting" in out


def test_stop_meet_returns_summary_when_present():
    meet_bot._status["summary"] = "Decisions: ship Friday."
    assert asyncio.run(meet_bot.stop_meet()) == "Decisions: ship Friday."
