"""A conversation that stopped hours ago is not the conversation being had now.

From the log, the morning after an evening of work:

    you (voice)> Good morning.
    jarvis>      Initiate Deep Research Sequence for tech event team name ideas?

The topic from the night before, offered as though the greeting had been a request to continue
it. The history was restored with no regard for its age.
"""
from __future__ import annotations

import json
import time
import types

import pytest

from jarvis.agent import groq_core


def _agent(tmp_path, monkeypatch):
    core = groq_core.GroqAgent.__new__(groq_core.GroqAgent)
    core.messages = [{"role": "system", "content": "standing prompt"}]
    monkeypatch.setattr(core, "_history_path", lambda: tmp_path / "chat-history.json",
                        raising=False)
    return core


def _write(path, turns, saved_at=None):
    payload = turns if saved_at is None else {"saved_at": saved_at, "turns": turns}
    path.write_text(json.dumps(payload), encoding="utf-8")


TURNS = [
    {"role": "user", "content": "help me name a tech event team"},
    {"role": "assistant", "content": "How about Neural Collective?"},
]


def test_a_recent_conversation_is_restored(tmp_path, monkeypatch):
    core = _agent(tmp_path, monkeypatch)
    _write(tmp_path / "chat-history.json", TURNS, saved_at=time.time() - 60)
    core._restore_history()
    assert any("tech event team" in m["content"] for m in core.messages)


def test_last_nights_conversation_is_not(tmp_path, monkeypatch):
    """This is the bug: "Good morning" answered with the previous night's topic."""
    core = _agent(tmp_path, monkeypatch)
    _write(tmp_path / "chat-history.json", TURNS,
           saved_at=time.time() - groq_core.HISTORY_STALE_AFTER_S - 60)
    core._restore_history()
    assert not any("tech event team" in m["content"] for m in core.messages)
    assert core.messages == [{"role": "system", "content": "standing prompt"}]


def test_the_boundary_is_hours_not_minutes():
    """Long enough to go to lunch and carry on; short enough that overnight is a clean start."""
    assert 2 * 3600 <= groq_core.HISTORY_STALE_AFTER_S <= 8 * 3600


def test_history_written_before_this_change_still_loads(tmp_path, monkeypatch):
    """It was a bare list. An upgrade must not throw away a conversation in progress."""
    core = _agent(tmp_path, monkeypatch)
    _write(tmp_path / "chat-history.json", TURNS)          # no stamp at all
    core._restore_history()
    assert any("tech event team" in m["content"] for m in core.messages)


def test_restored_turns_are_told_the_topic_is_over(tmp_path, monkeypatch):
    """The old fence only said the numbers were stale, so the model kept the subject."""
    core = _agent(tmp_path, monkeypatch)
    _write(tmp_path / "chat-history.json", TURNS, saved_at=time.time() - 60)
    core._restore_history()
    fence = next(m for m in core.messages if "EARLIER session" in m.get("content", ""))
    assert "do not continue its topic" in fence["content"]
    assert "STALE" in fence["content"]


def test_a_missing_or_broken_file_is_survivable(tmp_path, monkeypatch):
    core = _agent(tmp_path, monkeypatch)
    core._restore_history()                                 # nothing there
    (tmp_path / "chat-history.json").write_text("{not json", encoding="utf-8")
    core._restore_history()
    assert core.messages == [{"role": "system", "content": "standing prompt"}]


def test_saving_records_when(tmp_path, monkeypatch):
    core = _agent(tmp_path, monkeypatch)
    core.messages = [{"role": "system", "content": "p"},
                     {"role": "user", "content": "hello"},
                     {"role": "assistant", "content": "hi"}]
    core._save_history()
    written = json.loads((tmp_path / "chat-history.json").read_text())
    assert isinstance(written, dict) and written["saved_at"] > 0
    assert [t["content"] for t in written["turns"]] == ["hello", "hi"]
