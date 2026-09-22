"""A muted microphone, which is silent in a way no gain setting can fix.

Found live rather than imagined: the mic had been muted at the system level, so every rung of the
recovery ladder ran, none of them helped, and what came back was "sorry sir, I didn't catch that"
— instead of the one sentence that would have solved it in two seconds.
"""

from __future__ import annotations

import subprocess

import pytest

from jarvis.audio import inputs


class FakeRun:
    """Stands in for wpctl."""

    def __init__(self, out="Volume: 1.00", code=0):
        self.out = out
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))

        class Result:
            returncode = self.code
            stdout = self.out
            stderr = ""

        return Result()


def test_a_muted_source_is_noticed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun("Volume: 1.53 [MUTED]"))
    assert inputs.is_muted() is True


def test_an_open_source_is_not_called_muted(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun("Volume: 1.00"))
    assert inputs.is_muted() is False


def test_no_wpctl_means_no_opinion(monkeypatch):
    """None, not False. "I cannot tell" and "it is fine" are different answers, and treating the
    first as the second sends the ladder down three rungs that cannot help."""
    def explode(*_a, **_k):
        raise FileNotFoundError("no wpctl here")

    monkeypatch.setattr(subprocess, "run", explode)
    assert inputs.is_muted() is None


def test_a_failed_wpctl_means_no_opinion_either(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun("", code=1))
    assert inputs.is_muted() is None


def test_unmuting_asks_wireplumber_not_alsa(monkeypatch):
    """The mute a person actually toggles — the keyboard key, the slider in Settings — lives in
    wireplumber, and ALSA can report a perfectly open capture chain underneath one."""
    states = iter(["Volume: 1.00 [MUTED]", "Volume: 1.00"])
    fake = FakeRun()

    def run(argv, **kwargs):
        if "get-volume" in argv:
            fake.out = next(states)
        return fake(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert inputs.unmute() is True
    assert any("set-mute" in " ".join(call) for call in fake.calls)


def test_unmuting_something_that_is_not_muted_changes_nothing(monkeypatch):
    fake = FakeRun("Volume: 1.00")
    monkeypatch.setattr(subprocess, "run", fake)
    assert inputs.unmute() is False
    assert not any("set-mute" in " ".join(call) for call in fake.calls)


def test_the_mute_check_comes_before_the_other_rungs(monkeypatch):
    """Order is the fix. Reopening the device and calming the gain both run happily against a
    muted microphone and both report success against silence."""
    order: list[str] = []

    monkeypatch.setattr(inputs, "is_muted", lambda: (order.append("checked mute"), True)[1])
    monkeypatch.setattr(inputs, "unmute", lambda: (order.append("unmuted"), True)[1])
    monkeypatch.setattr(inputs, "refresh_devices",
                        lambda *a, **k: order.append("reopened"))
    monkeypatch.setattr(inputs, "calm_the_gain",
                        lambda: (order.append("calmed"), True)[1])
    monkeypatch.setattr(inputs, "sample", lambda *_a, **_k: b"")
    monkeypatch.setattr(inputs, "broken_without_anyone_speaking", lambda _s: False)

    healthy, what = inputs.recover()
    assert healthy
    assert "muted" in what
    assert order[:2] == ["checked mute", "unmuted"]
    assert "reopened" not in order, "it went on fiddling after fixing the actual problem"


def test_an_unfixable_mute_says_what_the_person_can_do(monkeypatch):
    """If Jarvis cannot lift it, the reply has to name the thing that can."""
    monkeypatch.setattr(inputs, "is_muted", lambda: True)
    monkeypatch.setattr(inputs, "unmute", lambda: False)
    monkeypatch.setattr(inputs, "sample", lambda *_a, **_k: b"")
    monkeypatch.setattr(inputs, "broken_without_anyone_speaking", lambda _s: True)

    healthy, what = inputs.recover()
    assert healthy is False
    assert "muted" in what
    assert "mute key" in what or "Settings" in what
