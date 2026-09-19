"""Dictation: what gets typed, and what stops it.

Two complaints, both from using it: the phrase that starts dictation was being typed into the
document, and nothing the user said would make it stop.
"""
from __future__ import annotations

import pytest

from jarvis import dictation


@pytest.mark.parametrize("said", [
    "jarvis terminate", "terminate", "jarvis stop", "stop", "jarvis cancel", "abort",
    "stop dictation", "end dictation", "exit dictation", "dictation off", "dictation over",
    "done dictating", "that's enough", "that is enough", "ruko", "bas karo",
])
def test_these_stop_it(said):
    """"Terminate" and a bare "stop" are what people reach for when a mode will not end, and
    neither was recognised — so the word meant to stop the typing was typed instead."""
    assert dictation.wants_to_stop(said) is True


@pytest.mark.parametrize("said", [
    "stop the car", "cancel my subscription please", "terminate the process on port 8080",
    "I had enough of that film",
])
def test_these_are_sentences_someone_is_dictating(said):
    """In dictation a sentence that looks like a command is just a sentence being written."""
    assert dictation.wants_to_stop(said) is False


@pytest.mark.parametrize("said", [
    "jarvis dictate mode", "dictate mode", "start dictation", "ok dictate mode",
    "dictation", "jarvis, dictation mode",
])
def test_the_trigger_is_recognised_when_it_comes_round_again(said):
    """It arrives a second time more often than it should — the tail of the same sentence in the
    next capture, or a repeat because nothing visibly happened."""
    assert dictation.is_the_trigger(said) is True


@pytest.mark.parametrize("said", [
    "the dictation was long and rambling and about many different things",
    "he asked me to take dictation for the whole afternoon which was tedious",
    "write this down for me please",
])
def test_ordinary_prose_is_not_mistaken_for_the_trigger(said):
    """Typed text that happens to contain the word must still be typed."""
    assert dictation.is_the_trigger(said) is False


def test_starting_still_works():
    for said in ("jarvis dictate mode", "start dictation", "take a note", "dictate"):
        assert dictation.wants_to_start(said) is True


def test_stopping_beats_typing():
    """A phrase that both stops and could be typed must stop."""
    assert dictation.wants_to_stop("terminate") is True
