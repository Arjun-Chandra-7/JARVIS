"""LinkedIn means the copilot, not the website.

"Open my LinkedIn" used to go to linkedin.com, because "open X" is handled by the generic opener
and LinkedIn is a website like any other. It is technically what was asked for and never what was
wanted.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis import linkedin_command as lc


@pytest.mark.parametrize("said, screen", [
    ("open my linkedin", "dashboard"),
    ("linkedin", "dashboard"),
    ("my linkedin", "dashboard"),
    ("my linkedin profile", "profile"),
    ("my linkedin portfolio", "profile"),
    ("open my linked in page", "profile"),
    ("show me my linkedin drafts", "approvals"),
    ("linkedin approvals", "approvals"),
    ("take me to linkedin calendar", "calendar"),
    ("go to my linkedin network", "network"),
    ("check my linkedin followers", "analytics"),
    ("open linkedin analytics", "analytics"),
    ("open linked in settings", "settings"),
])
def test_looking_at_linkedin_opens_the_right_copilot_screen(said, screen):
    assert lc.parse(said) == screen


@pytest.mark.parametrize("said", [
    "post this on linkedin",
    "draft a linkedin post about jarvis",
    "schedule a linkedin post for friday",
    "send a linkedin message to maya",
    "approve the linkedin draft",
])
def test_doing_something_with_linkedin_is_left_to_its_own_tools(said):
    """Turning "post this on LinkedIn" into "here is a dashboard" would be a step backwards."""
    assert lc.parse(said) is None


@pytest.mark.parametrize("said", [
    "I saw a funny linkedin post about recruiters today",
    "he messaged me on linkedin yesterday and I never replied",
    "open netflix",
    "what is the time",
])
def test_these_are_not_requests_to_open_it(said):
    assert lc.parse(said) is None


def test_the_more_specific_screen_wins():
    """"My network analytics" is analytics, not network."""
    assert lc.parse("show my linkedin network analytics") == "analytics"


def test_a_copilot_that_is_not_running_is_reported_rather_than_faked(monkeypatch):
    from jarvis.integrations import linkedin as integration
    monkeypatch.setattr(integration, "_ensure", lambda: "The LinkedIn copilot backend isn't answering.")
    said = asyncio.run(lc.handle("open my linkedin"))
    assert "isn't answering" in said


def test_a_browser_that_will_not_open_is_not_reported_as_success(monkeypatch):
    from jarvis.integrations import linkedin as integration
    monkeypatch.setattr(integration, "_ensure", lambda: None)
    monkeypatch.setattr(integration, "open_console", lambda _view: False)
    said = asyncio.run(lc.handle("open my linkedin"))
    assert "couldn't open" in said


def test_it_says_which_screen_it_opened(monkeypatch):
    from jarvis.integrations import linkedin as integration
    monkeypatch.setattr(integration, "_ensure", lambda: None)
    monkeypatch.setattr(integration, "open_console", lambda _view: True)
    assert "profile" in asyncio.run(lc.handle("my linkedin profile"))
    assert asyncio.run(lc.handle("open my linkedin")).endswith("copilot, sir.")


def test_it_runs_before_the_generic_opener():
    """Otherwise "open my LinkedIn" is taken by open_command and lands on linkedin.com."""
    from jarvis.commands import deterministic_handlers
    names = [n for n, _ in deterministic_handlers()]
    assert names.index("linkedin") < names.index("open")
    assert names.index("linkedin") < names.index("find_site")
