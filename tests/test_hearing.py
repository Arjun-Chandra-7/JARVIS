"""Hearing the command words — the biasing that decides whether "whiteboard" arrives at all."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("said, echoed", [
    ("of an image whiteboard, dictate, dictation, sketch, canvas", True),
    ("whiteboard, Mona Lisa, draw me", True),
    ("find a free whiteboard site and draw me the mona lisa", False),
    ("open netflix, then youtube", False),
    ("", False),
])
def test_the_vocabulary_read_back_as_speech_is_thrown_away(said, echoed):
    """Biasing hard enough to work occasionally comes back as the list itself. It is not a
    plausible sentence and must never reach the handlers as a command."""
    from jarvis.audio.local_stt import DEFAULT_VOCABULARY, _is_the_vocabulary_echoed_back
    assert _is_the_vocabulary_echoed_back(said, DEFAULT_VOCABULARY) is echoed


def test_every_caller_gets_the_command_words():
    """The console and the meeting recorder pass no vocabulary, and the fallback used to be an
    older copy with no command words in it — so those two paths heard "wide board"."""
    from jarvis.audio.local_stt import DEFAULT_VOCABULARY
    from jarvis.config import Config
    for word in ("whiteboard", "dictate", "Mona Lisa", "draw me", "Netflix"):
        assert word in DEFAULT_VOCABULARY
        assert word in Config().stt_vocabulary
