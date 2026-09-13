"""Trimming the context folds the dropped turns into a rolling summary instead of losing them."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agent.groq_core import GroqAgent


class _Client:
    """Captures the prompt the summariser sends and returns a canned summary."""

    def __init__(self, reply="Arjun booked the 14:30 review and asked for a flight to Delhi."):
        self.reply = reply
        self.prompts: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.prompts.append(kw["messages"][-1]["content"])
        msg = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    a = GroqAgent.__new__(GroqAgent)
    a.model = "test"
    a.summary = ""
    a.client = _Client()
    a.messages = [{"role": "system", "content": "sys"}]
    return a


def _turns(n: int) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"question {i}"})
        out.append({"role": "assistant", "content": f"answer {i}"})
    return out


def test_short_conversations_are_not_trimmed(agent):
    agent.messages += _turns(3)
    assert agent._trim() == []
    assert len(agent.messages) == 7


def test_trim_returns_what_it_dropped(agent):
    agent.messages += _turns(12)
    before = len(agent.messages)
    dropped = agent._trim()
    assert dropped
    assert len(agent.messages) < before
    assert agent.messages[0]["content"] == "sys"


def test_trim_resumes_on_a_user_turn(agent):
    # Splitting a tool_calls/tool pair makes the API reject the next request, so the kept suffix
    # must start on a clean user message.
    agent.messages += _turns(12)
    agent._trim()
    assert agent.messages[1]["role"] == "user"


def test_dropped_turns_are_folded_into_the_summary(agent):
    agent.messages += _turns(12)
    dropped = agent._trim()
    agent._summarise(dropped)
    assert "Delhi" in agent.summary
    prompt = agent.client.prompts[0]
    assert "question 0" in prompt and "answer 0" in prompt


def test_the_previous_summary_is_carried_into_the_next_one(agent):
    agent.summary = "Earlier: Arjun prefers evening meetings."
    agent._summarise([{"role": "user", "content": "and book the flight"}])
    assert "Arjun prefers evening meetings" in agent.client.prompts[0]


def test_summary_persists_across_instances(agent, tmp_path, monkeypatch):
    agent._summarise([{"role": "user", "content": "something worth keeping"}])
    assert agent.summary

    other = GroqAgent.__new__(GroqAgent)
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    assert other._load_summary() == agent.summary


def test_summarising_nothing_is_a_no_op(agent):
    agent._summarise([])
    assert agent.summary == ""
    assert agent.client.prompts == []


def test_tool_messages_are_not_summarised(agent):
    # Tool payloads are long, machine-shaped and already reflected in the assistant's reply.
    agent._summarise([
        {"role": "tool", "tool_call_id": "1", "content": "{'cpu': 31.2, 'mem': 68}"},
        {"role": "user", "content": "thanks"},
    ])
    assert "cpu" not in agent.client.prompts[0].split("New exchanges to fold in:")[1]


def test_a_failing_summariser_leaves_the_summary_untouched(agent):
    agent.summary = "kept"

    def boom(**_kw):
        raise RuntimeError("model down")

    agent.client.chat.completions.create = boom
    agent._summarise([{"role": "user", "content": "x"}])
    assert agent.summary == "kept"


def test_summary_is_sent_to_the_model_as_context(agent):
    agent.summary = "Arjun's flight is on the 24th."
    agent._router_on = False
    agent.schemas = []
    captured = {}

    def create(**kw):
        captured["messages"] = kw["messages"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])

    agent.client.chat.completions.create = create
    agent._complete([], memo="")
    systems = [m["content"] for m in captured["messages"] if m["role"] == "system"]
    assert any("flight is on the 24th" in s for s in systems)
