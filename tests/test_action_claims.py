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


# ----------------------------------------------------------------- vague excuses
@pytest.mark.parametrize("reply", [
    "It seems there's a temporary glitch.",
    "It appears there's a temporary issue opening the app.",
    "Something went wrong on my end.",
    "There was a technical issue — please try again later.",
    "Sorry, a hiccup there.",
])
def test_vague_excuses_are_detected(reply):
    assert ac.invents_an_excuse(reply), reply


@pytest.mark.parametrize("reply", [
    "No installed app matches Networks. Did you mean Opera GX?",
    "I couldn't find that on the page.",
    "Opera GX is running without control enabled.",
    "Your battery is at 100 percent.",
    "I can read the brightness but not change it.",
])
def test_specific_reasons_are_not_excuses(reply):
    assert not ac.invents_an_excuse(reply), reply


def test_real_reason_replaces_the_excuse():
    out = ac.real_reason("It seems there's a temporary glitch.",
                         "No installed app matches “Networks”. Did you mean: Opera GX?")
    assert "Networks" in out and "glitch" not in out


def test_real_reason_strips_the_model_only_prefix():
    out = ac.real_reason("glitch", 'WRONG TOOL. “netflix” is a website, not an application.')
    assert out.startswith("“netflix”")


def test_real_reason_keeps_the_reply_when_there_is_nothing_better():
    assert ac.real_reason("It seems there's a glitch.", "") == "It seems there's a glitch."


# ------------------------------------------------- claims about settings, not just launches
@pytest.mark.parametrize("reply", [
    "Brightness set to 30%.",            # no "I" at all, so the clause pattern never saw it
    "I set the brightness to 30%.",      # plain "I", which the pattern required to be "I've"
    "I turned the brightness down.",     # words between the verb and its particle
    "Volume is now 40%.",
])
def test_a_settings_change_is_a_claim(reply):
    assert ac.claims_an_action(reply)


@pytest.mark.parametrize("reply", [
    "I can set the brightness if you like.",
    "I couldn't change the brightness.",
    "Your battery is at 100%.",
    "Your next meeting is at 3pm.",
])
def test_offers_and_refusals_and_readings_are_not_claims(reply):
    assert not ac.claims_an_action(reply)


def test_no_stray_control_characters_in_the_patterns():
    """A \\b written through a non-raw string becomes a backspace byte and silently stops
    matching — the pattern still compiles, so nothing complains."""
    import re as _re

    for name in dir(ac):
        obj = getattr(ac, name)
        if isinstance(obj, _re.Pattern):
            assert not set(obj.pattern) & set("\x07\x08\x0b\x0c"), name


# --------------------------------------------- the admission should carry the reason with it
def test_the_stated_reason_is_kept():
    """"Nothing was carried out" leaves the user with nothing to act on; the blocker names the
    group to join and the command that joins it."""
    out = ac.honest_fallback(
        "[FAILURE] I can read the brightness but not change it: nvidia_0 is owned by the "
        "'video' group. Run sudo usermod -aG video $USER.")
    assert "[FAILURE]" not in out
    assert "usermod -aG video" in out
    assert out.startswith("I didn't actually manage")
    assert "I can read" in out          # the reason keeps its own capitalisation


def test_without_a_reason_it_still_admits_plainly():
    assert ac.honest_fallback() == \
        "I didn't actually manage to do that, sir — nothing was carried out."


# ---------------------------------------------------- answering about something never asked
_CAST_ABOUT = "I cannot set brightness directly. Please use `open_app` for web browsers."


@pytest.mark.parametrize("request_text", [
    "Jarvis opened the first result then",      # all three verbatim from one session's log
    "Hey, John, this is Open Wikipedia",
    "He always set my volume to 70%",
])
def test_an_answer_about_brightness_nobody_asked_for_is_caught(request_text):
    assert ac.is_a_non_sequitur(request_text, _CAST_ABOUT)


@pytest.mark.parametrize("request_text,reply", [
    ("set brightness to 30", "I cannot set brightness directly."),   # asked about, so allowed
    ("what is the volume", "Volume is at 35%."),
    ("turn up the brightness", "Brightness is at 60%."),
    ("open netflix", "Opened Netflix."),
    ("whats the weather", "It is sunny and 24 degrees."),
])
def test_a_relevant_answer_is_left_alone(request_text, reply):
    assert not ac.is_a_non_sequitur(request_text, reply)


def test_an_internal_tool_name_is_never_read_out():
    assert ac.is_a_non_sequitur("anything at all", "Please use `browser_type` instead.")


# --------------------------------------------- the guard belongs to actions, not conversation
@pytest.mark.parametrize("said", [
    "how are you doing",        # answered "I didn't actually manage to do that, sir"
    "No, do I ask you?",        # same, a minute later
    "what can you do",
    "tell me a joke",
    "what's the weather",
    "how do I open a file",     # contains "open", still a question
])
def test_a_question_is_not_a_failed_action(said):
    """No tool runs when someone asks how you are, and that is the right outcome."""
    assert not ac.asks_for_an_action(said)


@pytest.mark.parametrize("said", [
    "open netflix",
    "click the allow button",
    "set the volume to 30",
    "can you open netflix",     # phrased as a question, plainly a request
    "play the latest video on youtube",
])
def test_a_request_still_counts_as_one(said):
    assert ac.asks_for_an_action(said)
