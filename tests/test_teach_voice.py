"""The teaching overlay inside the real voice loop: lessons, follow-ups and barge-in through
``VoiceSession._turns``, with the microphone and speaker scripted (see
test_voice_conversation_flow). Proves the wiring — that a lesson never reaches the brain, that a
follow-up changes the lesson on screen, that talking over a lesson pauses it and that a cough
does not end it.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from jarvis.audio import voice_session
from jarvis.teach import runner as runner_mod
from jarvis.teach.bus import Overlay, _NullTransport, use
from jarvis.teach.runner import LessonRunner, Spoken
from test_voice_conversation_flow import Brain, make_session, run_conversation

AREA = {"index": 0, "scale": 1.0, "w": 1920, "h": 1080, "work": {"x": 0, "y": 28, "w": 1920, "h": 1052}}


@pytest.fixture()
def ov(monkeypatch):
    o = use(Overlay(_NullTransport()))
    monkeypatch.setattr(o, "work_area", lambda monitor="primary": AREA)
    r = LessonRunner(o)
    old = runner_mod.RUNNER
    runner_mod.RUNNER = r
    yield o
    r._cancel_cleanup()
    runner_mod.RUNNER = old


def lesson_voice(s, barge_after=None):
    """Replace the session's phrase speaker: every phrase 'plays' at once, and optionally the
    person talks over phrase ``barge_after``."""
    s.lesson_said = []
    state = {"barge_after": barge_after}

    def speak(phrases, on_start):
        s._barge = None
        for i, p in enumerate(phrases):
            if state["barge_after"] is not None and len(s.lesson_said) == state["barge_after"]:
                state["barge_after"] = None
                s._barge = {"onset_at": 0.0, "triggered_at": 0.2, "stopped_at": 0.22, "frames": [[0] * 1280] * 3,
                            "policy": "aec", "detector_ms": 150.0, "during": "speaking"}
                return Spoken(i, max(0, i - 1), True)
            s.lesson_said.append(p)
            on_start(i, time.time() * 1000 + 30)
        return Spoken(len(phrases), len(phrases), False)
    s._speak_phrases = speak


def ops(ov):
    return [c["op"] for c in ov.transport.commands()]


def test_a_lesson_and_its_follow_ups_never_reach_the_brain(monkeypatch, ov):
    s = make_session(["show where chunking happens", "clear it", None], monkeypatch)
    lesson_voice(s)
    brain = Brain()
    run_conversation(s, brain, "Jarvis, explain RAG architecture with a diagram")
    assert brain.asked == []
    said = " ".join(s.lesson_said)
    assert "embedding model" in said.lower() and "Chunking happens here" in said
    o = ops(ov)
    assert o.count("scene.create") == 1                              # the follow-up did not redraw
    assert o[-1] == "scene.clear"
    assert ("reply", "Cleared.") in s.events or "Cleared." in s.spoken


def test_talking_over_a_lesson_pauses_it_and_go_back_replays(monkeypatch, ov):
    s = make_session(["go back one step", None], monkeypatch)
    lesson_voice(s, barge_after=6)
    run_conversation(s, Brain(), "Explain the Pythagoras theorem with a diagram")
    o = ops(ov)
    assert "timeline.pause" in o                                     # frozen when talked over
    assert o.count("scene.create") >= 2                              # "go back" restored a snapshot
    assert runner_mod.RUNNER.status in ("done", "idle")


def test_a_cough_during_a_lesson_does_not_end_it(monkeypatch, ov):
    s = make_session([None, None], monkeypatch)                      # the barge-in captured nothing
    lesson_voice(s, barge_after=4)
    run_conversation(s, Brain(), "Explain RAG architecture with a diagram")
    from jarvis.teach.lessons import rag
    total = len(rag.plan("en", AREA).spoken_segments)
    assert len(s.lesson_said) >= total                                # it carried on to the end
    assert runner_mod.RUNNER.status in ("done", "idle")


def test_ordinary_requests_still_go_to_the_brain(monkeypatch, ov):
    s = make_session([None], monkeypatch)
    lesson_voice(s)
    brain = Brain()
    run_conversation(s, brain, "what's the weather like today")
    assert brain.asked and not s.lesson_said and ops(ov) == []


def test_speak_phrases_reports_progress(monkeypatch):
    """The session's own phrase speaker: HUD gets each phrase as it is heard; a stop mid-way
    reports an interruption."""
    s = voice_session.VoiceSession.__new__(voice_session.VoiceSession)
    s.backend = "local"
    s.config = SimpleNamespace(piper_model="x.onnx", audio_output_device=-1)
    s.events = []
    s.on_event = lambda k, t="": s.events.append((k, t))
    s._played = []

    def fake_segments(phrases, model, dev, stop_event=None, on_first_audio=None, on_level=None, on_start=None, on_played=None):
        for i, p in enumerate(phrases[:2]):                          # stopped after two
            on_start(i, time.time() * 1000)
            on_played(p)

    monkeypatch.setattr("jarvis.audio.local_tts.speak_segments", fake_segments)
    s._while_speaking = lambda play, announce, force=False: play(None, None, None)
    starts = []
    res = s._speak_phrases(["one.", "two.", "three."], lambda i, at: starts.append(i))
    assert starts == [0, 1] and res.started == 2 and res.finished == 2 and res.interrupted
    assert ("reply", "one.") in s.events and ("reply", "two.") in s.events
