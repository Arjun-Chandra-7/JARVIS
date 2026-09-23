"""Long answers were cut off halfway while a lecture played aloud: its sound read as barge-in."""
import threading
from types import SimpleNamespace

from jarvis.audio.voice_session import VoiceSession


def _session(frames, playing):
    s = SimpleNamespace(threshold=0.01, sample_rate=16000, frame_length=1280, events=[])
    import time
    s._read_frame = lambda: (time.sleep(0.08), next(frames))[1]      # frames arrive in real time
    s._media_playing = lambda: playing
    s.on_event = lambda kind, text="": s.events.append(kind)
    return s


def test_media_playing_never_cuts_jarvis_off():
    loud = iter([[20000] * 1280] * 100)
    s = _session(loud, playing=True)
    stop = threading.Event()
    VoiceSession._barge_in_monitor(s, stop)
    assert not stop.is_set() and s.events == []


def test_a_short_burst_does_not_but_a_second_of_talking_does():
    quiet = [[50] * 1280] * 8                            # the echo, measured first
    burst = [[20000] * 1280] * 9 + [[50] * 1280] * 3      # 0.7 s: a laugh, a door
    talk = [[20000] * 1280] * 13                          # ~1 s of someone talking
    s = _session(iter(quiet + burst + talk), playing=False)
    stop = threading.Event()
    VoiceSession._barge_in_monitor(s, stop)
    assert stop.is_set() and s.events == ["barge_in"]


def test_media_status_is_read_from_mpris(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="Paused\nPlaying\n"))
    assert VoiceSession._media_playing()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="Paused\n"))
    assert not VoiceSession._media_playing()
