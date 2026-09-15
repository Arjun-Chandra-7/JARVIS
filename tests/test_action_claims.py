"""A reply must not claim an action that never happened.

Every "claim" case below is text Jarvis actually produced while doing nothing at all.
"""
import pytest

from jarvis.agent import action_claims as ac


@pytest.mark.parametrize("reply", [
    "Opening Opera GX...",
    "Opening YouTube...",
    "Opening Networks...",
    "Opening Friends on Netflix...",
    "I've opened Netflix for you.",
    "I'm launching Spotify now.",
    "Playing the next episode.",
    "Sent the message to Pradhuman.",
    "Setting the volume to 40 percent.",
    "I have turned on do not disturb.",
    "Clicked the profile.",
    "Searching Netflix for Friends.",
])
def test_action_claims_are_detected(reply):
    assert ac.claims_an_action(reply), reply


@pytest.mark.parametrize("reply", [
    "Would you like me to open Netflix?",
    "Shall I open that for you?",
    "I can open Netflix if you want.",
    "I'll open it once you confirm.",
    "I couldn't open Opera GX — it isn't installed.",
    "I cannot open that.",
    "Which one did you mean?",
    "Please specify which profile.",
    "Your battery is at 100 percent.",
    "The weather in Bangalore is 28 degrees.",
    "Hello, how can I help?",
    "I don't know that one.",
    "I was unable to open it.",
])
def test_non_claims_are_left_alone(reply):
    assert not ac.claims_an_action(reply), reply


def test_empty_reply_is_not_a_claim():
    assert not ac.claims_an_action("")
    assert not ac.claims_an_action("   ")
    assert not ac.claims_an_action(None)


def test_correction_names_the_offending_reply():
    msg = ac.correction_for("Opening Opera GX...")
    assert "Opening Opera GX" in msg
    assert "did not call any tool" in msg


def test_fallback_admits_nothing_happened():
    text = ac.honest_fallback()
    assert "didn't" in text.lower() or "did not" in text.lower()
    # It must not imply success.
    assert not ac.claims_an_action(text)


def test_a_failure_report_is_not_a_claim():
    """The honest failure path must survive the check, or it would be rewritten as a failure."""
    assert not ac.claims_an_action(
        "I couldn't find an app called that. Did you mean Opera GX?")
