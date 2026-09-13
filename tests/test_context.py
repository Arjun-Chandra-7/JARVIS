"""Desktop context: window list, attention, media, and whether it is a good time to speak."""

from __future__ import annotations

import pytest

from jarvis import context

WMCTRL = (
    "0x01c00004  0 code.code             Bhramastra Welcome - Linkdin - Visual Studio Code\n"
    "0x01c0000b  0 code.code             Bhramastra Release Notes: 1.137.0 - Jarvis - Visual Studio Code\n"
    "0x02200003  1 google-chrome.Google-chrome  Bhramastra Awwwards - Google Chrome\n"
)


@pytest.fixture(autouse=True)
def clear_cache():
    context._CACHE.clear()
    yield
    context._CACHE.clear()


@pytest.fixture
def quiet(monkeypatch):
    """No windows, at the keyboard, nothing playing — each test turns on what it needs."""
    monkeypatch.setattr(context, "_run", lambda *_a, **_k: "")
    monkeypatch.setattr(context, "windows", lambda: [])
    monkeypatch.setattr(context, "active_window", lambda: None)
    monkeypatch.setattr(context, "idle_seconds", lambda: 1.0)
    monkeypatch.setattr(context, "idle_inhibited", lambda: False)
    monkeypatch.setattr(context, "media", lambda: None)


def test_wmctrl_parsing_extracts_app_and_title():
    got = context._parse_wmctrl(WMCTRL)
    assert [w["app"] for w in got] == ["code", "code", "Google-chrome"]
    assert got[0]["title"] == "Welcome - Linkdin - Visual Studio Code"
    assert got[2]["desktop"] == 1


def test_wmctrl_parsing_ignores_junk_lines():
    assert context._parse_wmctrl("not a window line\n\n") == []


def test_active_window_matches_a_non_padded_id(monkeypatch):
    # xprop reports 0x1c00004; wmctrl zero-pads it to 0x01c00004. Compare numerically.
    monkeypatch.setattr(context, "windows", lambda: context._parse_wmctrl(WMCTRL))
    monkeypatch.setattr(context, "_run",
                        lambda *_a, **_k: "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1c0000b")
    got = context.active_window()
    assert got and "Release Notes" in got["title"]


def test_active_window_is_none_for_a_wayland_native_window(monkeypatch):
    # The focused window is not in the X11 list at all — the documented limitation.
    monkeypatch.setattr(context, "windows", lambda: context._parse_wmctrl(WMCTRL))
    monkeypatch.setattr(context, "_run",
                        lambda *_a, **_k: "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x400003")
    assert context.active_window() is None


def test_activity_is_inferred_from_wm_class():
    assert context._activity_for("code") == "coding"
    assert context._activity_for("zoom") == "in a call"
    assert context._activity_for("nothing-known") is None
    # WM_CLASS casing is inconsistent between apps — wmctrl reports "google-chrome.Google-chrome" —
    # so matching has to be case-insensitive.
    assert context._activity_for("Google-chrome") == "browsing"
    assert context._activity_for("google-chrome") == "browsing"


def test_snapshot_falls_back_to_open_apps_when_focus_is_unknown(quiet, monkeypatch):
    monkeypatch.setattr(context, "windows", lambda: context._parse_wmctrl(WMCTRL))
    s = context.snapshot()
    assert s["active"] is None
    assert s["activity"] == "coding"        # inferred from the app list instead
    assert s["apps"][:1] == ["code"]


def test_snapshot_marks_away_after_five_minutes(quiet, monkeypatch):
    monkeypatch.setattr(context, "idle_seconds", lambda: 400.0)
    assert context.snapshot()["away"] is True


def test_snapshot_is_not_away_while_active(quiet):
    assert context.snapshot()["away"] is False


def test_describe_reads_as_a_sentence(quiet, monkeypatch):
    monkeypatch.setattr(context, "windows", lambda: context._parse_wmctrl(WMCTRL))
    monkeypatch.setattr(context, "media",
                        lambda: {"player": "spotify", "artist": "Aphex Twin", "title": "Xtal",
                                 "playing": True})
    text = context.describe()
    assert text.startswith("At the keyboard")
    assert "coding" in text and "Xtal" in text and text.endswith(".")


def test_describe_says_when_the_x11_list_is_empty(quiet):
    assert "native Wayland apps are not listed" in context.describe()


def test_interruptible_when_at_the_keyboard(quiet):
    ok, _why = context.is_interruptible()
    assert ok is True


def test_not_interruptible_during_a_call(quiet, monkeypatch):
    monkeypatch.setattr(context, "idle_inhibited", lambda: True)
    ok, why = context.is_interruptible()
    assert ok is False and "awake" in why


def test_not_interruptible_when_away(quiet, monkeypatch):
    monkeypatch.setattr(context, "idle_seconds", lambda: 900.0)
    ok, why = context.is_interruptible()
    assert ok is False and "away" in why


def test_not_interruptible_with_a_call_app_focused(quiet, monkeypatch):
    monkeypatch.setattr(context, "windows",
                        lambda: [{"id": "0x1", "desktop": 0, "app": "zoom", "title": "Zoom Meeting"}])
    ok, why = context.is_interruptible()
    assert ok is False and "call app" in why


def test_a_player_with_no_track_is_ignored(monkeypatch):
    # KDE Connect registers a player for the paired phone whether or not anything is playing.
    outputs = {
        ("playerctl", "-l"): "kdeconnect.mpris_x",
        ("playerctl", "status"): "Playing",
        ("playerctl", "metadata", "--format", "{{playerName}}\t{{artist}}\t{{title}}"): "kdeconnect\t\t",
    }
    monkeypatch.setattr(context, "_run", lambda cmd, **_k: outputs.get(tuple(cmd), ""))
    assert context.media() is None


def test_media_is_reported_when_a_track_is_playing(monkeypatch):
    outputs = {
        ("playerctl", "-l"): "spotify",
        ("playerctl", "status"): "Playing",
        ("playerctl", "metadata", "--format", "{{playerName}}\t{{artist}}\t{{title}}"):
            "spotify\tAphex Twin\tXtal",
    }
    monkeypatch.setattr(context, "_run", lambda cmd, **_k: outputs.get(tuple(cmd), ""))
    m = context.media()
    assert m == {"player": "spotify", "artist": "Aphex Twin", "title": "Xtal", "playing": True}


def test_probes_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(context, "_run", lambda *a, **k: calls.append(1) or WMCTRL)
    context.windows()
    context.windows()
    assert len(calls) == 1


def test_a_missing_binary_yields_empty_not_an_exception(monkeypatch):
    monkeypatch.setattr(context.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("wmctrl")))
    assert context.windows() == []
    assert context.idle_seconds() is None
