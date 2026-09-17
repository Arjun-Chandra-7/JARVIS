"""Handing work to a coding agent in the editor, where it can be watched."""
from __future__ import annotations

import pytest

from jarvis import coding_command as cc
from jarvis.coding import quota, roster, session


# ------------------------------------------------------------------ which agent, which model
def test_the_order_of_preference_is_claude_then_codex_then_antigravity():
    assert roster.PREFERENCE == ("claude", "codex", "agy")


def test_a_hard_problem_gets_the_high_effort_model():
    agent, model, effort = roster.pick("why is the websocket dropping under load",
                                       {"claude": True, "codex": True, "agy": True})
    assert (agent.name, model, effort) == ("claude", "sonnet", "high")


def test_a_small_change_gets_the_cheap_setting():
    agent, model, effort = roster.pick("fix a typo in the readme",
                                       {"claude": True, "codex": True, "agy": True})
    assert (agent.name, model, effort) == ("claude", "opus", "low")


def test_ordinary_work_sits_in_the_middle():
    _agent, model, effort = roster.pick("add a dark mode toggle to the settings page",
                                        {"claude": True, "codex": True, "agy": True})
    assert (model, effort) == ("opus", "medium")


def test_an_exhausted_agent_is_skipped_rather_than_started():
    """Starting a job on an agent with nothing left means stopping halfway through it."""
    agent, _m, _e = roster.pick("add a feature", {"claude": False, "codex": True, "agy": True})
    assert agent.name == "codex"


def test_antigravity_only_ever_offers_one_model_at_one_effort():
    agent, model, effort = roster.pick("fix a typo", {"claude": False, "codex": False, "agy": True})
    assert (agent.name, model, effort) == ("agy", "gemini-3.8", "high")


def test_nothing_available_is_said_rather_than_guessed():
    assert roster.pick("anything", {"claude": False, "codex": False, "agy": False}) is None


def test_being_asked_for_an_agent_by_name_wins():
    """Asking for Codex and being handed Claude is infuriating."""
    agent, _m, _e = roster.pick("add a login form", {"claude": True, "codex": True, "agy": True},
                                prefer="codex")
    assert agent.name == "codex"


@pytest.mark.parametrize("said, name", [
    ("tell claude to refactor this", "claude"),
    ("ask codex to add a test", "codex"),
    ("get antigravity to look at it", "agy"),
    ("use gemini for this one", "agy"),
    ("now we need to add a button", None),
])
def test_an_agent_named_in_the_request_is_recognised(said, name):
    assert roster.named_in(said) == name


# ------------------------------------------------------------------ the command line typed out
@pytest.mark.parametrize("agent, model, effort, must_contain", [
    ("claude", "opus", "medium", ["claude", "--model opus", "--effort medium"]),
    ("codex", "gpt-5.6-sol", "low", ["codex", "-m gpt-5.6-sol", 'model_reasoning_effort="low"']),
    ("agy", "gemini-3.8", "high", ["agy", "--model gemini-3.8", "--effort high"]),
])
def test_the_launch_line_uses_flags_these_tools_actually_have(agent, model, effort, must_contain):
    line = session.launch_command(agent, model, effort, "/tmp/work")
    for part in must_contain:
        assert part in line


def test_the_agent_is_started_interactively_not_one_shot():
    """`claude --print` answers once and exits, which makes every follow-up a new conversation
    with no memory of the last — the opposite of the point."""
    line = session.launch_command("claude", "opus", "medium", "/tmp/work")
    assert "--print" not in line


def test_the_workspace_is_quoted():
    line = session.launch_command("claude", "opus", "low", "/tmp/a dir with spaces")
    assert "'/tmp/a dir with spaces'" in line


# ------------------------------------------------------------------ what counts as a request
@pytest.mark.parametrize("said, work", [
    ("ok but now we need to add a dark mode toggle", "add a dark mode toggle"),
    ("now we need to fix the login bug", "fix the login bug"),
    ("tell claude to refactor the parser", "refactor the parser"),
    ("lets add a test for the endpoint", "add a test for the endpoint"),
    ("can you delete that folder", "delete that folder"),
])
def test_these_are_work_for_an_agent(said, work):
    assert cc.parse(said) == work


@pytest.mark.parametrize("said", [
    "we should go out for dinner",
    "we need to buy milk",
    "open netflix",
    "what is the time",
    "now we need to leave",
])
def test_these_are_not(said):
    """"We should add a retry to the upload" and "we should go out for dinner" have the same
    shape, and only one of them is for a coding agent."""
    assert cc.parse(said) is None


def test_a_bare_imperative_only_counts_mid_conversation():
    """Otherwise "delete all my screenshots" looks like a coding task."""
    session.end()
    assert cc.parse("add a retry to the upload") is None
    agent = roster.BY_NAME["claude"]
    session.begin(agent, "opus", "medium", "/tmp")
    try:
        assert cc.parse("add a retry to the upload") == "add a retry to the upload"
    finally:
        session.end()


# ------------------------------------------------------------------ one conversation, not many
def test_the_model_is_chosen_once_and_left_alone():
    """An agent whose model changes mid-conversation contradicts its own earlier answers."""
    session.end()
    live = session.begin(roster.BY_NAME["claude"], "opus", "medium", "/tmp")
    live.touch()
    live.touch()
    same = session.current()
    assert (same.model, same.effort, same.turns) == ("opus", "medium", 2)
    session.end()


def test_a_conversation_left_alone_for_long_enough_is_over():
    import time
    session.end()
    live = session.begin(roster.BY_NAME["claude"], "opus", "medium", "/tmp")
    live.last = time.time() - session.STALE_AFTER_S - 1
    assert session.current() is None


# ------------------------------------------------------------------ what the budget check can know
def test_quota_reports_a_standing_for_every_agent():
    for name in roster.PREFERENCE:
        standing = quota.standing(name)
        assert isinstance(standing.ready, bool)


def test_an_agent_that_is_not_installed_is_not_offered():
    assert quota.standing("definitely-not-installed").ready is False
