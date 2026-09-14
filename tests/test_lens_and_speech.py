"""Context lens and the stop-speaking signal."""
import pytest

from jarvis.audio import levels, speech_control
from jarvis.integrations import lens


# ---------------------------------------------------------------- lens
def test_unknown_lens_is_rejected():
    r = lens.capture("telepathy")
    assert not r["ok"] and "Unknown lens" in r["error"]


def test_empty_selection_explains_what_to_do(monkeypatch):
    monkeypatch.setattr(lens, "selection", lambda: None)
    r = lens.capture("selection")
    assert not r["ok"]
    assert "Highlight" in r["error"], "an empty capture must say how to fix it"


def test_selection_is_returned_and_capped(monkeypatch):
    monkeypatch.setattr(lens, "selection", lambda: "x" * (lens.MAX_CHARS * 2))
    r = lens.capture("selection")
    assert r["ok"] and len(r["text"]) == lens.MAX_CHARS


def test_clipboard_empty_is_reported(monkeypatch):
    monkeypatch.setattr(lens, "clipboard", lambda: None)
    r = lens.capture("clipboard")
    assert not r["ok"] and "empty" in r["error"].lower()


def test_screen_failure_is_reported_not_faked(monkeypatch):
    monkeypatch.setattr(lens, "screen", lambda *a, **k: None)
    r = lens.capture("screen")
    assert not r["ok"]
    assert "Could not read the screen" in r["error"]


def test_missing_tool_returns_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr(lens.shutil, "which", lambda _n: None)
    assert lens.selection() is None
    assert lens.clipboard() is None


# ---------------------------------------------------------------- stop speaking
@pytest.fixture(autouse=True)
def tmp_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(speech_control, "_PATH", None)
    monkeypatch.setattr(levels, "_PATH", None)
    yield


def test_stop_requested_after_capture_is_seen():
    token = speech_control.token()
    speech_control.request_stop()
    assert speech_control.should_stop(token)


def test_stop_requested_before_capture_does_not_silence_the_next_utterance():
    """The bug a plain flag would cause: a stale stop muting everything Jarvis says afterwards."""
    speech_control.request_stop()
    token = speech_control.token()          # new utterance starts here
    assert not speech_control.should_stop(token)


def test_repeated_stops_each_register():
    token = speech_control.token()
    speech_control.request_stop()
    speech_control.request_stop()
    assert speech_control.should_stop(token)


def test_speaking_flag_round_trips():
    assert not speech_control.is_speaking()
    speech_control.set_speaking(True)
    assert speech_control.is_speaking()
    speech_control.set_speaking(False)
    assert not speech_control.is_speaking()


def test_request_stop_reports_whether_anything_was_speaking():
    speech_control.set_speaking(False)
    assert speech_control.request_stop() is False
    speech_control.set_speaking(True)
    assert speech_control.request_stop() is True


# ---------------------------------------------------------------- audio levels
def test_level_round_trip():
    levels.publish(0.42, "listening", 0.9)
    got = levels.read()
    assert got["level"] == pytest.approx(0.42)
    assert got["state"] == "listening"
    assert not got["stale"]


def test_level_is_clamped():
    levels.publish(5.0, "listening", -2.0)
    got = levels.read()
    assert got["level"] == 1.0
    assert got["speech"] == 0.0


def test_stale_level_reads_as_idle():
    """A voice process that dies must not leave the HUD animating a level nothing produces."""
    levels.publish(0.9, "listening")
    assert levels.read(max_age_s=-1.0)["stale"] is True
    assert levels.read(max_age_s=-1.0)["level"] == 0.0


def test_missing_level_file_is_idle():
    levels.clear()
    got = levels.read()
    assert got["level"] == 0.0 and got["stale"] is True
