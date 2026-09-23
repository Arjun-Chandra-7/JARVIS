"""Answering "is my microphone working".

Every piece of this existed already — the mute check, the probe, the recovery ladder. What was
missing was the question, so the way you learned your microphone was muted was by being told
"sorry sir, I didn't catch that" until you gave up. That is how it actually went.

The two failures worth caring about are opposites, and both are pinned below: calling a silent
microphone fine wastes somebody's afternoon, and calling a quiet room broken sends them off to
fix something that was never wrong.
"""

from __future__ import annotations

import pytest

from jarvis.audio import inputs, mic_report


class FakeHeard:
    def __init__(self, peak: float):
        self.peak = peak
        self.frames = 10
        self.loud = 0


@pytest.fixture
def mic(monkeypatch):
    """A microphone whose mute state and level this test decides."""
    def configure(peak: float, muted, unmute_works: bool = True):
        monkeypatch.setattr(inputs, "is_muted", lambda: muted)
        monkeypatch.setattr(inputs, "sample",
                            lambda *_a, **_k: FakeHeard(peak))
        monkeypatch.setattr(inputs, "unmute", lambda: unmute_works)
    return configure


# ------------------------------------------------------------------ is it even the question?
@pytest.mark.parametrize("said", [
    "is my mic working",
    "is the microphone working",
    "check my mic",
    "is my mike broken",
    "can you hear me, is the mic on",
    "test the microphone",
    "is my mic muted",
    "what's wrong with my microphone",
])
def test_these_are_asking_about_the_microphone(said):
    assert mic_report.asked(said)


@pytest.mark.parametrize("said", [
    "mute the music",                  # about volume, not the input
    "what's the weather",
    "turn the mic volume up",          # a command, handled elsewhere
    "",
])
def test_these_are_not(said):
    assert not mic_report.asked(said)


def test_a_remark_with_no_verb_is_not_a_question():
    assert not mic_report.asked("the microphone on this laptop is a cheap one")


# --------------------------------------------------------------------------- the answers
def test_a_muted_microphone_is_unmuted_and_said_so(mic):
    """The case that actually happened. The answer arrives with the fix already applied,
    because "yes it's muted" alone would leave the person exactly where they started."""
    mic(peak=0.02, muted=True)
    answer = mic_report.check()
    assert "muted" in answer.lower()
    assert "unmuted it" in answer.lower()


def test_when_it_cannot_be_unmuted_it_says_how_to(mic):
    mic(peak=0.0, muted=True, unmute_works=False)
    answer = mic_report.check()
    assert "muted" in answer.lower()
    assert "mute key" in answer.lower() or "settings" in answer.lower()


def test_a_quiet_room_is_not_a_broken_microphone(mic):
    """The expensive false alarm: an idle probe of a healthy microphone is near-silent by
    definition, so quiet must never read as faulty."""
    mic(peak=0.004, muted=False)
    assert mic_report.check().startswith("Yes, sir")


def test_silence_is_reported_as_silence(mic):
    mic(peak=0.0, muted=False)
    answer = mic_report.check()
    assert answer.startswith("No, sir")
    assert "silence" in answer.lower()


def test_saturation_is_named_because_it_looks_like_working(mic):
    """+30 dB boost on +30 dB capture measured 0.84 where a healthy floor was 0.005. The level
    meter moves, so it looks fine, and every word arrives as noise."""
    mic(peak=0.84, muted=False)
    answer = mic_report.check()
    assert answer.startswith("No, sir")
    assert "saturated" in answer.lower()
    assert "gain" in answer.lower() or "boost" in answer.lower()


def test_an_unknown_mute_state_is_admitted_not_guessed(mic):
    """wpctl cannot always say. Reporting half an answer as a whole one is how a report stops
    being worth reading."""
    mic(peak=0.02, muted=None)
    answer = mic_report.check()
    assert "working" in answer.lower()
    assert "couldn't check" in answer.lower()


def test_a_question_about_mute_gets_an_answer_about_mute(mic):
    """"Is it muted?" answered with "it's working" is true and beside the point — and the point
    is the only reason anyone asks."""
    mic(peak=0.02, muted=False)
    assert "muted" in mic_report.check(said="is my mic muted").lower()
    # And the general question still gets the general answer.
    assert mic_report.check(said="is my mic working").startswith("Yes, sir")
