"""Settings the owner changes by asking: parsed in three languages, persisted, applied live,
verified against what the component reports — and never changed by anything but the owner."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from jarvis import preferences
from jarvis.settings import parse, registry, runtime
from jarvis.settings.command import handle as settings_handle


def run(coro):
    return asyncio.run(coro)


class Conversation:
    window_s = 8.0


class FakeSession:
    def __init__(self):
        self._conversation = Conversation()


def overlay_answers(values_applied=True, running=0, motion=None, delay=0.05):
    """Stand in for the overlay page: when the backend emits, report a measurement."""
    def respond(kind, text):
        payload = json.loads(text)
        values = payload["values"]
        target = runtime.report_path("overlay")    # fixed now: a late thread must not write into the next test

        def later():
            time.sleep(delay)
            m = motion or ("off" if values["overlay.animations"] is False else
                           "reduced" if values["motion.reduced"] else "full")
            target.write_text(json.dumps({
                "component": "overlay", "revision": payload["revision"], "values": values,
                "observed": {"motion": m, "running_animations": running if m == "off" else 3,
                             "intensity": values["overlay.intensity"], "visible": values["overlay.visible"]}}))
        threading.Thread(target=later, daemon=True).start()
        return True
    return respond


# ------------------------------------------------------------------ parsing, three languages
@pytest.mark.parametrize("said,setting,value", [
    ("Jarvis, disable your animations.", "overlay.animations", False),
    ("Animations wapas on kar do.", "overlay.animations", True),
    ("animations band kar do", "overlay.animations", False),
    ("एनिमेशन बंद करो", "overlay.animations", False),
    ("Turn off away-mode replies immediately.", "away.replies", False),
    ("Keep listening for twelve seconds.", "voice.follow_up_s", 12),
    ("12 second tak sunte raho", "voice.follow_up_s", 12),
    ("turn on reduced motion", "motion.reduced", True),
    ("hide the overlay", "overlay.visible", False),
    ("give shorter answers", "voice.verbosity", "brief"),
    ("turn off dictation history", "dictation.history", False),
])
def test_known_settings_are_recognised_in_english_hindi_and_hinglish(said, setting, value):
    req = parse.parse(said)
    assert req is not None and req.op == "set" and req.setting.id == setting
    assert req.setting.coerce(req.value) == value


@pytest.mark.parametrize("said,steps", [
    ("Speak slightly faster.", 1), ("Speak a little faster", 1), ("thoda tez bolo", 1),
    ("बोलने की गति थोड़ी तेज़ करो", 1), ("speak faster", 2), ("dheere bolo", -2), ("speak much slower", -3),
])
def test_speed_changes_are_bounded_steps(said, steps):
    req = parse.parse(said)
    assert req.op == "adjust" and req.setting.id == "voice.speed" and req.steps == steps
    assert 0.8 <= req.target() <= 1.3


def test_speaking_slightly_faster_is_one_small_step_not_an_extreme():
    assert parse.parse("speak slightly faster").target() == 1.05


def test_speed_never_leaves_its_range():
    preferences.update(lambda d: d.setdefault("settings", {}).update({"voice.speed": 1.3}))
    assert parse.parse("speak much faster").target() == 1.3


def test_a_timed_mute_has_an_end():
    req = parse.parse("Don't announce notifications for two hours.")
    assert req.setting.id == "notifications.level" and req.value == "quiet"
    assert 7100 < req.until - time.time() <= 7200


def test_the_teaching_pen_gets_dimmer():
    req = parse.parse("Make the teaching pen less bright.")
    assert req.setting.id == "teach.glow" and req.target() == pytest.approx(0.4)


@pytest.mark.parametrize("said", [
    "open youtube", "what is the speed of light", "stop the music", "turn off the lights",
    "send hi to papa", "notifications off", "don't announce Instagram for two hours",
])
def test_other_requests_are_not_settings(said):
    assert parse.parse(said) is None


# ------------------------------------------------------------------ persistence and undo
def test_a_change_persists_and_keeps_unrelated_preferences():
    preferences.set_pref("job_alerts", False)
    change = registry.set_value("voice.follow_up_s", 12, verify=False)
    assert change.old == registry.get("voice.follow_up_s").default_value()
    data = json.loads((preferences.state_dir() / "preferences.json").read_text())
    assert data["settings"]["voice.follow_up_s"] == 12
    assert data["job_alerts"] is False
    assert registry.value("voice.follow_up_s") == 12


def test_older_boolean_callers_still_read_booleans():
    registry.set_value("voice.speed", 1.1, verify=False)
    flags = preferences.load()
    assert set(flags) == {"notifications", "job_alerts"} and all(isinstance(v, bool) for v in flags.values())


def test_undo_restores_the_previous_value_and_walks_back():
    registry.set_value("voice.follow_up_s", 12, verify=False)
    registry.set_value("voice.follow_up_s", 20, verify=False)
    registry.undo(timeout=0)
    assert registry.value("voice.follow_up_s") == 12
    registry.undo(timeout=0)
    assert registry.value("voice.follow_up_s") == registry.get("voice.follow_up_s").default_value()
    assert registry.undo(timeout=0) is None


def test_a_temporary_value_runs_out_by_itself():
    registry.set_value("notifications.level", "quiet", until=time.time() + 60, verify=False)
    assert registry.value("notifications.level") == "quiet"
    assert not preferences.notifications_enabled()
    assert registry.value("notifications.level", now=time.time() + 61) == "all"


def test_the_legacy_notification_switch_is_kept_truthful():
    preferences.set_notifications(False)
    raw = json.loads((preferences.state_dir() / "preferences.json").read_text())
    assert raw["notifications"] is False and registry.value("notifications.level") == "quiet"
    preferences.set_notifications(True)
    assert preferences.notifications_enabled()


def test_values_outside_the_schema_are_refused_or_clamped():
    with pytest.raises(ValueError):
        registry.get("voice.verbosity").coerce("shouting")
    assert registry.get("voice.follow_up_s").coerce(500) == 30
    with pytest.raises(ValueError):
        registry.get("voice.speed").coerce("fast")


def test_every_setting_has_the_full_schema():
    for row in registry.describe_all():
        assert row["id"] and row["name"] and row["type"] in {"bool", "float", "int", "enum"}
        assert row["component"] in {"overlay", "voice", "backend", "away", "teach"}
        assert set(row["aliases"]) >= {"en", "hi", "hinglish"}
        assert row["reversible"] is True


# ------------------------------------------------------------------ verification
def test_animations_off_is_only_confirmed_when_the_overlay_measures_it(monkeypatch):
    monkeypatch.setattr(runtime, "_emit", overlay_answers(running=0))
    change = registry.set_value("overlay.animations", False, timeout=2)
    assert change.verified and change.verification.observed["running_animations"] == 0


def test_an_overlay_that_still_animates_fails_verification_and_the_change_is_put_back(monkeypatch):
    monkeypatch.setattr(runtime, "_emit", overlay_answers(running=2, motion="off"))
    change = registry.set_value("overlay.animations", False, timeout=2)
    assert change.verification.status == "failed" and change.restored
    assert registry.value("overlay.animations") is True
    assert registry.last_change() is None


def test_no_overlay_means_saved_but_unconfirmed(monkeypatch):
    change = registry.set_value("overlay.animations", False, timeout=0.2)
    assert change.verification.status == "unconfirmed"
    assert registry.value("overlay.animations") is False


def test_the_voice_reports_the_speed_its_engine_will_use():
    change_holder = {}

    def voice_process():
        # What the voice process's watcher does after a change: push, read back, report.
        from jarvis.settings import live

        for _ in range(40):
            if preferences.revision() >= 1:
                live.apply_once(FakeSession())
                return
            time.sleep(0.05)

    t = threading.Thread(target=voice_process, daemon=True)
    t.start()
    change_holder["c"] = registry.set_value("voice.speed", 1.05, timeout=3)
    t.join()
    change = change_holder["c"]
    assert change.verified, change.verification
    from jarvis.audio import kokoro_tts
    assert change.verification.observed["neutral_speed"] == pytest.approx(1.05)
    assert kokoro_tts.effective_speed(kokoro_tts.BRISK) == pytest.approx(round(1.09 * 1.05, 3))


def test_the_follow_up_window_is_pushed_into_the_live_conversation():
    from jarvis.settings import live

    session = FakeSession()
    registry.set_value("voice.follow_up_s", 12, verify=False)
    reported = live.apply_once(session)
    assert session._conversation.window_s == 12.0 and reported["voice.follow_up_s"] == 12


def test_the_pen_glow_reaches_every_lesson():
    from jarvis.teach import scene

    change = registry.set_value("teach.glow", 0.4)
    assert change.verified
    assert scene.create("anything", theme={"glow": 0.9, "palette": "jarvis"})["theme"] == \
        {"glow": 0.4, "palette": "jarvis"}


def test_dictation_history_setting_is_what_the_recorder_consults():
    from jarvis.flow import history

    registry.set_value("dictation.history", False, verify=False)
    assert history.enabled() is False


def test_the_away_emergency_stop_blocks_replies_and_lifting_it_needs_a_yes():
    from jarvis.approvals import MANAGER

    reply = run(settings_handle("Turn off away-mode replies immediately.", None, "voice"))
    assert "nothing more will be sent" in reply
    from jarvis.away_mode import engine
    assert engine.kill_switch(runtime._away_store())
    reply = run(settings_handle("turn away mode replies back on", None, "voice"))
    assert "yes" in reply.lower() and MANAGER.pending("voice")
    assert engine.kill_switch(runtime._away_store())        # not lifted until approved


# ------------------------------------------------------------------ through the command path
def test_case_a_disable_animations_through_the_real_command_path(monkeypatch):
    from jarvis.commands import handle

    monkeypatch.setattr(runtime, "_emit", overlay_answers(running=0))
    reply = run(handle("Jarvis, disable your animations.", None, "voice"))
    assert "animations are off" in reply.lower() and "couldn't confirm" not in reply
    assert registry.value("overlay.animations") is False
    reply = run(handle("Undo that", None, "voice"))
    assert reply.startswith("Undone") and "back on" in reply
    assert registry.value("overlay.animations") is True


def test_case_b_speak_a_little_faster_through_the_real_command_path():
    from jarvis.commands import handle
    from jarvis.settings import live

    def voice_process():
        for _ in range(60):
            if registry.value("voice.speed") != 1.0:
                live.apply_once(FakeSession())
                return
            time.sleep(0.05)

    threading.Thread(target=voice_process, daemon=True).start()
    monkeypatch_timeout = runtime.DEFAULT_TIMEOUT["voice"]
    runtime.DEFAULT_TIMEOUT["voice"] = 3.0
    try:
        reply = run(handle("Speak a little faster.", None, "voice"))
    finally:
        runtime.DEFAULT_TIMEOUT["voice"] = monkeypatch_timeout
    assert "a little faster" in reply and "1.05" in reply and "couldn't confirm" not in reply
    assert json.loads((preferences.state_dir() / "preferences.json").read_text())["settings"]["voice.speed"] == 1.05


def test_an_unconfirmed_change_is_never_reported_as_done():
    reply = run(settings_handle("Keep listening for twelve seconds.", None, "voice"))
    assert reply.startswith("Saved") and "couldn't confirm" in reply


@pytest.mark.parametrize("source", ["whatsapp", "telegram", "away", "unknown-session"])
def test_only_the_owners_front_ends_may_change_settings(source):
    assert run(settings_handle("disable your animations", None, source)) is None
    assert registry.value("overlay.animations") is True and registry.last_change() is None


def test_asking_for_what_is_already_set_says_so_in_plain_english():
    assert run(settings_handle("animations wapas on kar do", None, "voice")) == "Animations are already on."


def test_an_undo_with_nothing_to_undo_says_so():
    assert "no settings change" in run(settings_handle("undo the last preference change", None, "voice"))
