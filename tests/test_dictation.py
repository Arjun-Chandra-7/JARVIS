"""In dictation, a sentence that looks like a command is a sentence someone is writing."""
import pytest

from jarvis import dictation as d


@pytest.mark.parametrize("said", [
    "jarvis dictate", "dictate", "start dictation", "dictation mode",
    "take a note", "take this down", "type what I say", "likho",
])
def test_these_start_it(said):
    assert d.wants_to_start(said)


@pytest.mark.parametrize("said", [
    "open netflix", "what is the volume", "dictate the terms of the treaty",
    "take a look at this", "",
])
def test_these_do_not(said):
    assert not d.wants_to_start(said)


@pytest.mark.parametrize("said", [
    "stop dictation", "stop dictating", "end dictation", "done dictating",
    "that's enough", "jarvis stop typing", "bas karo",
])
def test_these_end_it(said):
    assert d.wants_to_stop(said)


@pytest.mark.parametrize("said", [
    "the meeting was enough of a success",   # contains "enough", is not the stop phrase
    "please stop the music",
    "I was dictating earlier",
])
def test_ordinary_prose_does_not_end_it(said):
    """The phrase that ends dictation has to be deliberate, or writing becomes impossible."""
    assert not d.wants_to_stop(said)


@pytest.mark.parametrize("spoken,written", [
    ("hello there comma how are you question mark", "hello there, how are you?"),
    ("this is a sentence full stop", "this is a sentence."),
    ("first line new line second line", "first line\nsecond line"),
    ("wait exclamation mark", "wait!"),
    ("open bracket an aside close bracket", "(an aside)"),
])
def test_spoken_punctuation_becomes_punctuation(spoken, written):
    assert d.as_typed(spoken) == written


def test_a_command_dictated_is_typed_not_obeyed():
    """The whole point: these words go on the page."""
    assert d.as_typed("open netflix and turn the volume up") == \
        "open netflix and turn the volume up"
