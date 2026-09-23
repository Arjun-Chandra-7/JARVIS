"""Barge-in: stop Jarvis within a few frames when the person talks, and not for anything else.

Earlier contract, and why it changed: barge-in was loudness alone for ~1 s, and stood down
entirely while a video played, because a lecture is louder than Jarvis's echo. A second is far
too long to feel heard, and "never while media plays" meant a lecture-watcher could not interrupt
at all. Now it is Silero speech probability above a continuously tracked background (the echo,
the video), for 2–4 frames. These tests pin both halves: it fires fast on the person, and not on
the video, a fan, or Jarvis himself.

Synthetic frames with an injected speech probability: no audio hardware, no model.
"""
import threading
from types import SimpleNamespace

import numpy as np

from jarvis.audio import bargein
from jarvis.audio.voice_session import VoiceSession

F = 0.08  # seconds per frame


def level(frame):
    return bargein.rms(frame)


def frame_at(rms):
    return (np.ones(1280) * rms * 32767).astype(np.int16).tolist()


def feed(detector, items, t0=0.0):
    """items: (rms, probability). Returns the index of the frame that triggered, or None."""
    for i, (r, p) in enumerate(items):
        if detector.feed(r, p, t0 + (i + 1) * F):
            return i
    return None


# ----------------------------------------------------------------- the detector
def test_the_person_talking_over_jarvis_stops_him_within_three_frames():
    d = bargein.BargeInDetector(bargein.policy(aec=False, media=False))
    d.arm()
    echo = [(0.01, 0.9)] * 10               # his own voice: speech-like, at echo level
    person = [(0.05, 0.95)] * 5              # louder than the echo, and speech
    hit = feed(d, echo + person)
    assert hit == len(echo) + 2              # third frame of the person
    assert abs(d.decided_in_s - 3 * F) < 1e-6


def test_with_echo_cancellation_two_frames_are_enough():
    d = bargein.BargeInDetector(bargein.policy(aec=True, media=False))
    d.arm()
    hit = feed(d, [(0.002, 0.1)] * 6 + [(0.03, 0.9)] * 3)
    assert hit == 7
    assert d.decided_in_s <= 0.25            # 160 ms of speech, inside the 150–250 ms target


def test_jarvis_own_echo_never_triggers_it():
    d = bargein.BargeInDetector(bargein.policy(aec=False, media=False))
    d.arm()
    # Speech-like and steady — the echo of a long answer, rising and falling a little.
    echo = [(0.01 + 0.004 * np.sin(i / 3), 0.95) for i in range(200)]
    assert feed(d, echo) is None


def test_the_first_frames_of_his_voice_arriving_are_learnt_not_judged():
    d = bargein.BargeInDetector(bargein.policy(aec=False, media=False))
    d.arm()
    feed(d, [(0.001, 0.0)] * 5)              # quiet room while thinking
    d.arm()                                  # first audio: the echo is about to arrive
    assert feed(d, [(0.012, 0.95)] * 40) is None


def test_a_lecture_playing_aloud_does_not_cut_him_off():
    d = bargein.BargeInDetector(bargein.policy(aec=False, media=True))
    d.arm()
    lecture = [(0.03 + 0.01 * np.sin(i / 2), 0.9) for i in range(300)]   # loud, and it is speech
    assert feed(d, lecture) is None


def test_the_person_can_still_interrupt_over_a_lecture():
    d = bargein.BargeInDetector(bargein.policy(aec=False, media=True))
    d.arm()
    lecture = [(0.03, 0.9)] * 30
    assert feed(d, lecture + [(0.1, 0.95)] * 6) == 33      # four frames, well above the video


def test_keys_fans_and_doors_are_loud_but_not_speech():
    d = bargein.BargeInDetector(bargein.policy(aec=False, media=False))
    d.arm()
    assert feed(d, [(0.005, 0.2)] * 10 + [(0.2, 0.1)] * 20) is None


def test_a_single_cough_is_too_short():
    d = bargein.BargeInDetector(bargein.policy(aec=False, media=False))
    d.arm()
    assert feed(d, [(0.005, 0.1)] * 10 + [(0.1, 0.9)] * 2 + [(0.005, 0.1)] * 10) is None


# ----------------------------------------------------------------- the monitor in the session
class FakeVad:
    def __init__(self, probs):
        self.probs = probs

    def reset(self):
        pass

    def push(self, _frame):
        return [self.probs.pop(0) if self.probs else 0.0]


def _session(frames, probs, media=False, aec=False):
    it = iter(frames)
    s = SimpleNamespace(sample_rate=16000, frame_length=1280, events=[], _media_on=media,
                        aec_active=aec, _barge=None, _handoff=threading.Event(),
                        _barge_vad=FakeVad(list(probs)))
    s._read_frame = lambda: next(it)
    s.on_event = lambda kind, text="": s.events.append(kind)
    s._handoff.set()                   # the loop takes the microphone over at once
    return s


def test_the_monitor_stops_jarvis_and_keeps_the_start_of_what_was_said():
    echo = [frame_at(0.01)] * 12
    person = [frame_at(0.06)] * 6
    s = _session(echo + person + [frame_at(0.06)] * 10, [0.9] * 12 + [0.95] * 16)
    stop, first_audio = threading.Event(), threading.Event()
    first_audio.set()
    VoiceSession._barge_in_monitor(s, stop, first_audio)
    assert stop.is_set() and s.events == ["barge_in"]
    kept = s._barge["frames"]
    # The onset is in what is handed over: the interruption's first word is not lost.
    assert sum(1 for f in kept if abs(level(f) - 0.06) < 0.005) >= 3
    assert s._barge["during"] == "speaking"


def test_the_monitor_ignores_the_video_when_nothing_cancels_it():
    lecture = [frame_at(0.03)] * 120
    s = _session(lecture + [None], [0.9] * 120, media=True)
    stop = threading.Event()
    first_audio = threading.Event()
    first_audio.set()
    s._read_frame = (lambda it: (lambda: next(it) if True else None))(iter(lecture))

    def read():
        try:
            return next(frames)
        except StopIteration:
            stop.set()
            return frame_at(0.0)
    frames = iter(lecture)
    s._read_frame = read
    VoiceSession._barge_in_monitor(s, stop, first_audio)
    assert s.events == [] and s._barge is None


def test_media_status_is_read_from_mpris(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="Paused\nPlaying\n"))
    assert VoiceSession._media_playing()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="Paused\n"))
    assert not VoiceSession._media_playing()
