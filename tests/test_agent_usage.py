"""Reading how much an agent has left out of what it prints."""
from __future__ import annotations

import pytest

from jarvis.coding import usage

# Verbatim from `claude --print "/usage"` on this machine.
CLAUDE_REAL = """You are currently using your subscription to power your Claude Code usage

Current session: 44% used · resets Sep 18, 2am (Asia/Kolkata)
Current week (all models): 46% used · resets Sep 22, 2:29pm (Asia/Kolkata)

What's contributing to your limits usage?
Last 24h · 41 requests · 1 session
  100% of your usage was at >150k context
"""


def test_the_fullest_window_decides_what_is_left():
    """Being fine for the week is no help when the five-hour session is spent."""
    assert usage.read_percent(CLAUDE_REAL) == 54.0


def test_a_percentage_of_usage_is_not_a_percentage_remaining():
    """Both turn up in the wild and they mean opposite things."""
    assert usage.read_percent("97% used this week") == 3.0
    assert usage.read_percent("you have 12% remaining") == 12.0
    assert usage.read_percent("3% left") == 3.0


def test_percentages_that_are_not_about_quota_do_not_count_as_a_balance():
    """"100% of your usage was at >150k context" is a breakdown, not a balance — and if it were
    read as one it would say the account was empty."""
    assert usage.read_percent("100% of your usage was at >150k context") is None


def test_nothing_reported_is_not_zero():
    assert usage.read_percent("no numbers at all here") is None
    assert usage.read_percent("") is None


@pytest.mark.parametrize("percent, start, hand_over", [
    (100.0, True, False),
    (54.0, True, False),
    (16.0, True, False),
    (15.0, False, False),
    (5.0, False, True),
    (1.0, False, True),
])
def test_the_two_thresholds(percent, start, hand_over):
    left = usage.Left(percent)
    assert left.enough_to_start() is start
    assert left.nearly_out() is hand_over


def test_an_agent_that_will_not_say_is_still_allowed_to_start():
    """Silence is not evidence of an empty account, and dropping the one agent that might have
    worked, on a guess, leaves nothing to do the job with."""
    unknown = usage.Left(None)
    assert unknown.known is False
    assert unknown.enough_to_start() is True
    assert unknown.nearly_out() is False


def test_each_agent_is_asked_in_its_own_words():
    assert usage.ASK["claude"] == "/usage"
    assert usage.ASK["codex"] == "/status"


def test_the_window_worth_repeating_is_picked_out():
    assert usage._first_window(CLAUDE_REAL).startswith("Current session: 44% used")
