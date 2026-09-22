"""Getting coding work to the orchestration in the first place.

The agent picking, the model picking, the token budgeting and the prompt rewriting were all
built and all unreachable: `parse` only matched polite lead-ins — "we need to", "can you",
"tell claude to" — so every natural phrasing returned None, fell through to the brain, and the
brain asked which agent and which model. Which is exactly what the orchestration exists to stop
anybody being asked.

The two halves of this file are equally important. Widening what counts as coding work is easy;
not swallowing "delete all my screenshots" is the part that needs the tests.
"""

from __future__ import annotations

import pytest

from jarvis import coding_command as cc


@pytest.fixture(autouse=True)
def no_live_session(monkeypatch):
    """No agent mid-conversation, which is the hard case — a running session makes any
    imperative count, and that is not what was broken."""
    monkeypatch.setattr(cc.session, "current", lambda: None)


@pytest.mark.parametrize("said,expected", [
    # Bare imperatives — how the work is nearly always phrased.
    ("fix the bug in voice_session", "fix the bug in voice_session"),
    ("build me a login page in this project", "build me a login page in this project"),
    ("write a migration for the users table", "write a migration for the users table"),
    ("refactor the auth module", "refactor the auth module"),
    # An agent addressed by name, with and without the comma.
    ("claude, refactor the auth module", "refactor the auth module"),
    ("codex add a dark mode toggle to the ui", "add a dark mode toggle to the ui"),
    # "an agent" as well as a named one.
    ("get an agent to fix the failing tests", "fix the failing tests"),
    # The polite forms that already worked, which must keep working.
    ("can you fix the failing tests", "fix the failing tests"),
    ("let's add a retry to the upload", "add a retry to the upload"),
])
def test_coding_work_reaches_the_orchestration(said, expected):
    assert cc.parse(said) == expected


@pytest.mark.parametrize("said", [
    "open netflix",
    "play some music",
    "remind me to call mum",
    "make me a coffee",
    "turn the lights off",
    "what's on my calendar today",
])
def test_ordinary_requests_are_left_alone(said):
    assert cc.parse(said) is None


def test_a_verb_alone_is_not_enough_without_a_conversation():
    """"delete the migration" and "delete all my screenshots" share a verb, and only one is for
    a coding agent. The noun is what separates them — which is why the guard is a noun."""
    assert cc.parse("delete the migration") is not None
    assert cc.parse("delete all my screenshots") is None


def test_a_running_conversation_makes_any_imperative_count(monkeypatch):
    """Once an agent is mid-conversation the context establishes what it is about, so the noun
    is no longer needed."""
    monkeypatch.setattr(cc.session, "current", lambda: object())
    assert cc.parse("delete all my screenshots") == "delete all my screenshots"


def test_nothing_at_all_is_not_work():
    assert cc.parse("") is None
    assert cc.parse("   ") is None


def test_the_agent_name_is_not_left_in_the_work():
    """It gets sent to the agent, so "claude, claude, fix this" would be its prompt."""
    work = cc.parse("claude, fix the failing tests")
    assert work is not None
    assert not work.lower().startswith("claude")


class TestNamingTheEditor:
    """Saying "in VS Code" is an instruction, not small talk.

    When it did not count as one, the request fell past this orchestration into the old job
    manager, whose answer was to ask which provider, which model and what effort. Two systems
    were listening for the same sentence and the wrong one kept winning.
    """

    def test_the_editor_named_is_enough_without_it_being_in_front(self, monkeypatch):
        from jarvis.coding import session, vscode

        monkeypatch.setattr(session, "current", lambda: None)
        monkeypatch.setattr(vscode, "window", lambda: None)
        monkeypatch.setattr(vscode, "active_window", lambda: None)
        for said in ("fix the login bug in VS Code",
                     "refactor the parser in vscode",
                     "add a test in the VS Code editor",
                     "debug the login flow with the editor"):
            assert cc._should_take_it(said), said

    def test_talking_about_the_editor_is_still_not_a_request(self, monkeypatch):
        """"Explain how VS Code extensions work" is a question, and belongs to the model."""
        from jarvis.coding import session, vscode

        monkeypatch.setattr(session, "current", lambda: None)
        monkeypatch.setattr(vscode, "window", lambda: None)
        monkeypatch.setattr(vscode, "active_window", lambda: None)
        for said in ("explain how vs code extensions work",
                     "what do you think of vs code"):
            assert not cc._should_take_it(said), said
