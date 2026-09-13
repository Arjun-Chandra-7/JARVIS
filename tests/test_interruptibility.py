"""Unprompted speech is gated on whether now is a reasonable moment."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.audio.voice_session import VoiceSession


@pytest.fixture
def session(monkeypatch, tmp_path):
    s = VoiceSession.__new__(VoiceSession)
    s.config = SimpleNamespace(vault_path=tmp_path, user_name="Arjun", kde_device_id="")
    s.spoken: list[str] = []
    s.events: list[tuple[str, str]] = []
    s._speak = lambda text, force=False: s.spoken.append(text)
    s.on_event = lambda kind, text="": s.events.append((kind, text))
    s._kc = None
    s._last_message = None
    return s


def _allow(session, ok: bool, why: str = "because"):
    session._good_moment = lambda: (ok, why)


def _event():
    return {"type": "message", "app": "WhatsApp", "title": "Meera", "text": "are you free?",
            "id": "1@s.whatsapp.net", "repliable": True, "handled": True}


def _patch_state(monkeypatch, away=False, notifications=True):
    monkeypatch.setattr("jarvis.agent.away.is_away", lambda _c: away)
    monkeypatch.setattr("jarvis.preferences.notifications_enabled", lambda: notifications)


def test_a_message_is_announced_at_a_good_moment(session, monkeypatch):
    _patch_state(monkeypatch)
    _allow(session, True)
    asyncio.run(session._handle_phone_event(_event(), agent=None))
    assert any("Meera" in t for t in session.spoken)


def test_a_message_is_held_during_a_call(session, monkeypatch):
    _patch_state(monkeypatch)
    _allow(session, False, "a call app is in the foreground")
    asyncio.run(session._handle_phone_event(_event(), agent=None))
    assert session.spoken == []
    held = [t for k, t in session.events if k == "phone" and t.startswith("held:")]
    assert held and "call app" in held[0]


def test_a_held_message_is_still_recorded_for_catch_up(session, monkeypatch):
    _patch_state(monkeypatch)
    _allow(session, False)
    asyncio.run(session._handle_phone_event(_event(), agent=None))
    assert session._last_message["who"] == "Meera"       # nothing is lost, only deferred


def test_muted_notifications_still_win_over_a_good_moment(session, monkeypatch):
    _patch_state(monkeypatch, notifications=False)
    _allow(session, True)
    asyncio.run(session._handle_phone_event(_event(), agent=None))
    assert session.spoken == []


def test_good_moment_defaults_to_yes_when_context_is_unavailable(session, monkeypatch):
    import jarvis.context as ctx

    monkeypatch.setattr(ctx, "is_interruptible",
                        lambda: (_ for _ in ()).throw(RuntimeError("no desktop")))
    ok, why = VoiceSession._good_moment(session)
    # Silence is the worse failure: a broken probe must not mute the assistant entirely.
    assert ok is True and "unavailable" in why


def test_good_moment_passes_through_the_context_verdict(session, monkeypatch):
    import jarvis.context as ctx

    monkeypatch.setattr(ctx, "is_interruptible", lambda: (False, "away from the keyboard"))
    assert VoiceSession._good_moment(session) == (False, "away from the keyboard")
