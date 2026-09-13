"""Suggestion chips reflect real state and are ranked by urgency."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from jarvis import suggestions

# Captured before the autouse fixture replaces it, so the parsing test exercises the real thing.
_REAL_UNREAD = suggestions._unread_whatsapp


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(
        vault_path=tmp_path,
        google_client_secret=tmp_path / "client_secret.json",
        google_token_file=tmp_path / "google-token.json",
    )


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """Default every probe to 'nothing happening'; each test turns on the one it cares about."""
    monkeypatch.setattr(suggestions, "_unread_whatsapp", lambda **_k: [])
    monkeypatch.setattr(suggestions, "_coding_trouble", lambda: [])
    monkeypatch.setattr(suggestions, "_battery", lambda: None)
    monkeypatch.setattr(suggestions, "_people_nearby", lambda: 0)
    monkeypatch.setattr(suggestions, "_google_needs_auth", lambda _c: False)


NOON = datetime(2026, 9, 13, 15, 0)   # mid-afternoon: no time-of-day chip


def labels(config, now=NOON, **kw):
    return [s["label"] for s in suggestions.build(config, now=now, **kw)]


def test_baseline_is_returned_when_nothing_is_happening(config):
    got = labels(config)
    assert "Catch me up" in got and "My agenda" in got


def test_every_suggestion_has_a_prompt_and_no_internal_priority(config):
    for s in suggestions.build(config, now=NOON):
        assert s["say"] and s["label"]
        assert "priority" not in s


def test_limit_is_respected(config):
    assert len(suggestions.build(config, now=NOON, limit=3)) == 3


def test_a_flat_battery_outranks_everything(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_battery", lambda: {"percent": 9, "plugged": False})
    assert labels(config)[0] == "Battery 9%"


def test_a_charging_battery_is_not_an_alert(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_battery", lambda: {"percent": 9, "plugged": True})
    assert not any(l.startswith("Battery") for l in labels(config))


def test_a_failing_build_is_surfaced(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_coding_trouble",
                        lambda: [{"status": "failed", "prompt": "refactor the parser"}])
    got = suggestions.build(config, now=NOON)
    assert got[0]["label"] == "Build failed"
    assert "refactor the parser" in got[0]["say"]


def test_several_failing_builds_are_counted(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_coding_trouble",
                        lambda: [{"status": "failed", "prompt": "a"}, {"status": "error", "prompt": "b"}])
    assert labels(config)[0] == "2 builds failed"


def test_one_unread_sender_is_named(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_unread_whatsapp",
                        lambda **_k: [{"name": "Meera", "text": "hi"}])
    assert "Reply to Meera" in labels(config)


def test_several_unread_senders_are_counted(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_unread_whatsapp",
                        lambda **_k: [{"name": "Meera"}, {"name": "Anish"}, {"name": "Prad"}])
    assert "Reply to 3 people" in labels(config)


def test_an_expired_google_session_is_surfaced(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_google_needs_auth", lambda _c: True)
    assert "Reconnect Google" in labels(config)


def test_morning_gets_a_briefing_chip(config):
    assert "Morning brief" in labels(config, now=datetime(2026, 9, 13, 7, 30))


def test_late_night_gets_a_wrap_up_chip(config):
    assert "Wrap up the day" in labels(config, now=datetime(2026, 9, 13, 23, 10))
    assert "Wrap up the day" in labels(config, now=datetime(2026, 9, 13, 1, 0))


def test_company_is_noticed(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_people_nearby", lambda: 3)
    assert "3 people here" in labels(config)


def test_urgent_items_come_before_routine_ones(config, monkeypatch):
    monkeypatch.setattr(suggestions, "_battery", lambda: {"percent": 8, "plugged": False})
    monkeypatch.setattr(suggestions, "_coding_trouble", lambda: [{"status": "failed", "prompt": "x"}])
    monkeypatch.setattr(suggestions, "_unread_whatsapp", lambda **_k: [{"name": "Meera"}])
    got = labels(config, now=datetime(2026, 9, 13, 7, 30))
    assert got[:4] == ["Battery 8%", "Build failed", "Reply to Meera", "Morning brief"]


def test_a_broken_probe_costs_one_chip_not_the_whole_list(config, monkeypatch):
    def boom():
        raise RuntimeError("sensor exploded")

    monkeypatch.setattr(suggestions, "_battery", boom)
    monkeypatch.setattr(suggestions, "_people_nearby", boom)
    got = labels(config)
    assert "Catch me up" in got
    assert not any(l.startswith("Battery") for l in got)


def test_group_and_outgoing_messages_are_ignored(monkeypatch):
    import json

    payload = json.dumps([
        {"from": "123@g.us", "name": "Family", "text": "hi", "ts": 9e12},
        {"from": "1@s.whatsapp.net", "name": "Meera", "text": "hi", "ts": 9e12, "fromMe": True},
        {"from": "2@s.whatsapp.net", "name": "Anish", "text": "hi", "ts": 9e12},
        {"from": "3@newsletter", "name": "News", "text": "hi", "ts": 9e12},
    ]).encode()

    class _Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _Resp())
    got = _REAL_UNREAD()
    assert [m["name"] for m in got] == ["Anish"]
