"""Fifteen specialists, and which one takes a request."""
import pytest

from jarvis.brains import choose
from jarvis.brains.roster import BY_NAME, ROSTER


def test_there_are_fifteen_of_them():
    assert len(ROSTER) == 15
    assert len({s.name for s in ROSTER}) == 15


@pytest.mark.parametrize("said,expected", [
    ("open netflix", "desk"),
    ("set the volume to 40", "desk"),
    ("write me an email to mum", "scribe"),
    ("why is this function throwing", "coder"),
    ("what is the latest news about mars", "researcher"),
    ("remind me to call dad tomorrow", "scheduler"),
    ("send a whatsapp to arjun", "messenger"),
    ("what did we discuss last time", "librarian"),
    ("how much disk have I got left", "analyst"),
    ("draw me a cat", "artist"),
    ("what does my screen say", "watcher"),
    ("explain how tcp works", "tutor"),
    ("how are you doing", "companion"),
])
def test_a_request_reaches_the_right_specialist(said, expected):
    assert choose.pick(said)[0].name == expected


@pytest.mark.parametrize("said", [
    "delete all my screenshots",
    "uninstall that application",
    "reboot the machine",
    "reset my settings",
    "format the drive",
])
def test_anything_destructive_goes_to_the_guardian(said):
    """The one case where a near-miss is not merely unhelpful."""
    specialist, confidence = choose.pick(said)
    assert specialist.name == "guardian"
    assert confidence == 1.0


def test_a_request_about_nothing_in_particular_still_lands_somewhere():
    specialist, confidence = choose.pick("hmm")
    assert specialist.name == "companion"
    assert confidence == 0.0


def test_a_phrase_outweighs_a_word():
    """"on my screen" is much stronger evidence than "screen"."""
    watcher = BY_NAME["watcher"]
    assert choose.score("what is on my screen", watcher) > choose.score("screen", watcher)


def test_length_does_not_buy_a_specialist_the_request():
    """A rambling sentence containing one cue should not outrank a short command about nothing
    else."""
    desk = BY_NAME["desk"]
    short = choose.score("open netflix", desk)
    rambling = choose.score(
        "I was wondering whether at some point you might consider whether it would be "
        "reasonable to open something for me, whenever that happens to suit you", desk)
    assert short > rambling


def test_commands_run_cold_and_conversation_does_not():
    assert BY_NAME["desk"].temperature == 0.0
    assert BY_NAME["guardian"].temperature == 0.0
    assert BY_NAME["companion"].temperature >= 0.5


def test_every_specialist_says_what_it_is_for():
    for s in ROSTER:
        assert s.does and s.instruction and s.cues
