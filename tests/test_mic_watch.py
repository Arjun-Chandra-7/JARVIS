"""Watching the microphone while Jarvis waits for its name.

From the log of 5–6 October: a capture stream raising PortAudioError on every read, retried at
once 8,730 times in an hour and never reopened.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from jarvis.audio import mic_watch
from jarvis.audio.voice_session import VoiceSession


@pytest.fixture(autouse=True)
def _metrics_elsewhere(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))


def test_failed_reads_wait_longer_each_time_up_to_a_ceiling():
    backoff = mic_watch.ReadBackoff()
    waits = [backoff.failed()[0] for _ in range(10)]
    assert waits[0] == mic_watch.FIRST_WAIT_S
    assert waits == sorted(waits)
    assert max(waits) == mic_watch.LONGEST_WAIT_S


def test_the_stream_is_reopened_only_after_several_failures_in_a_row():
    backoff = mic_watch.ReadBackoff()
    reopens = [backoff.failed()[1] for _ in range(mic_watch.REOPEN_AFTER * 2)]
    assert reopens.count(True) == 2
    assert reopens[mic_watch.REOPEN_AFTER - 1] is True


def test_a_good_read_starts_the_count_again():
    backoff = mic_watch.ReadBackoff()
    backoff.failed()
    backoff.failed()
    assert backoff.worked() == 2
    assert backoff.worked() is None
    assert backoff.failed()[0] == mic_watch.FIRST_WAIT_S


def _session():
    events = []
    reopened = []
    session = SimpleNamespace(
        _read_backoff=mic_watch.ReadBackoff(),
        _mic_lock=threading.Lock(),
        mic=SimpleNamespace(reopen=lambda: reopened.append(True)),
        on_event=lambda kind, text="": events.append((kind, text)),
    )
    session._reopen_mic = lambda: VoiceSession._reopen_mic(session)
    return session, events, reopened


def test_a_dead_stream_is_waited_out_and_reopened_not_hammered(monkeypatch):
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    session, events, reopened = _session()

    async def fail(times):
        for _ in range(times):
            await VoiceSession._mic_read_failed(session, OSError("PortAudioError"))

    asyncio.run(fail(mic_watch.REOPEN_AFTER))
    assert len(slept) == mic_watch.REOPEN_AFTER and all(s > 0 for s in slept)
    assert reopened == [True]
    # Said once for the outage, not once for every failed read.
    assert len([e for e in events if "read failed" in e[1]]) == 1


def test_a_reopen_that_fails_does_not_end_the_wait(monkeypatch):
    async def sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", sleep)
    session, events, _ = _session()

    def broken():
        raise OSError("no device")

    session.mic = SimpleNamespace(reopen=broken)

    async def fail():
        for _ in range(mic_watch.REOPEN_AFTER):
            await VoiceSession._mic_read_failed(session, OSError("PortAudioError"))

    asyncio.run(fail())
    assert any("couldn't reopen" in text for _, text in events)


def test_starting_with_the_microphone_muted_unmutes_it_and_says_so():
    lifted = []
    said = mic_watch.open_at_start(is_muted=lambda: True, unmute=lambda: lifted.append(1) or True)
    assert lifted and "unmuted" in said


def test_starting_with_an_open_microphone_says_nothing():
    assert mic_watch.open_at_start(is_muted=lambda: False, unmute=lambda: pytest.fail("unmuted")) is None


def test_no_way_to_tell_is_not_treated_as_muted():
    assert mic_watch.open_at_start(is_muted=lambda: None, unmute=lambda: pytest.fail("unmuted")) is None


def test_a_mute_that_will_not_lift_says_how_to_lift_it():
    said = mic_watch.open_at_start(is_muted=lambda: True, unmute=lambda: False)
    assert "couldn't unmute" in said and "mute key" in said


def test_a_mute_is_news_once_and_so_is_lifting_it():
    watch = mic_watch.MuteWatch()
    assert [watch.seen(m) for m in (False, True, True, None, True, False, False)] == [
        None, "muted", None, None, None, "unmuted", None]


def test_the_mute_shows_on_the_hud_and_is_not_spoken(monkeypatch):
    readings = iter([True, True, False])
    calls = {"n": 0}

    async def sleep(_seconds):
        calls["n"] += 1
        if calls["n"] > 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr("jarvis.audio.inputs.is_muted", lambda: next(readings))
    events = []
    session = SimpleNamespace(
        _mute_watch=mic_watch.MuteWatch(),
        config=SimpleNamespace(ptt_key="rightctrl"),
        on_event=lambda kind, text="": events.append((kind, text)),
        _speak=lambda *_a, **_k: pytest.fail("a mute must not be spoken over"),
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(VoiceSession._watch_mic_mute(session))
    said = [text for _, text in events]
    assert len(said) == 2
    assert "muted" in said[0] and "rightctrl" in said[0]
    assert "unmuted" in said[1]
