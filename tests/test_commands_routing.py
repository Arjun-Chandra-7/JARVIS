"""Milestone A regression tests: one command router, notification categories, truthful phone."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis import commands, preferences
from jarvis.config import Config


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    # Keep the coding-job fallthrough from touching the real ~/.config state file.
    from jarvis.integrations import coding_jobs
    monkeypatch.setattr(coding_jobs, "_DEFAULT_MANAGER",
                        coding_jobs.CodingJobManager(state_path=tmp_path / "coding-jobs.json"))
    yield


def run(text, config=None):
    return asyncio.run(commands.handle(text, config or Config()))


# --- notification toggle -------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "Jarvis turn off notifications",
    "turn off notifications please",
    "Jarvis mute notifications",
    "stop reading notifications",
    "disable all notifications",
])
def test_notifications_off_variations(phrase):
    assert "off" in run(phrase).lower()
    assert preferences.notifications_enabled() is False


@pytest.mark.parametrize("phrase", [
    "Jarvis turn on notifications",
    "unmute notifications",
    "turn notifications back on",
    "start reading notifications",
])
def test_notifications_on_variations(phrase):
    preferences.set_notifications(False)
    assert "on" in run(phrase).lower()
    assert preferences.notifications_enabled() is True


def test_toggle_preserves_other_preferences():
    preferences.set_job_alerts(False)
    run("Jarvis turn off notifications")
    data = json.loads((preferences.state_dir() / "preferences.json").read_text())
    assert data["notifications"] is False and data["job_alerts"] is False


def test_preference_survives_a_fresh_process():
    run("Jarvis mute notifications")
    code = ("import json,sys;from jarvis.preferences import notifications_enabled;"
            "sys.exit(0 if notifications_enabled() is False else 1)")
    assert subprocess.run([sys.executable, "-c", code], env={**os.environ}).returncode == 0


# --- notify category gating --------------------------------------------------

def test_notify_categories_are_independent():
    from jarvis.jobs import notify as notify_mod
    spoken = []
    notify_mod.set_announcer(spoken.append)
    try:
        preferences.set_notifications(False)
        preferences.set_job_alerts(True)
        notify_mod.notify("readout", "hi", speak=True)                       # muted
        notify_mod.notify("job done", "summary", speak=True, category="job")  # own toggle: on
        notify_mod.notify("battery critical", "2%", speak=True, category="critical")  # always
        assert spoken == ["job done. summary", "battery critical. 2%"]
    finally:
        notify_mod.set_announcer(None)


# --- phone: truthful state --------------------------------------------------

def test_open_phone_reports_failure_verbatim(monkeypatch):
    from jarvis.integrations import apps
    monkeypatch.setattr(apps, "phone_mirror", lambda: (False, "No phone detected over USB."))
    assert run("Jarvis open my phone") == "No phone detected over USB."


def test_open_phone_confirms_only_on_success(monkeypatch):
    from jarvis.integrations import apps
    monkeypatch.setattr(apps, "phone_mirror", lambda: (True, "scrcpy started"))
    assert run("open my phone") == "Opening your phone, sir."


# --- away mode: single owner, persisted ------------------------------------

def test_away_enter_and_exit_persist_state():
    config = Config()
    assert "away mode is on" in run("Jarvis I'm going out, handle my messages for me", config).lower()
    from jarvis.agent import away
    assert away.is_away(config) is True
    assert (Path(config.vault_path) / "Jarvis/private/away-state.json").exists()
    assert "welcome back" in run("Jarvis I'm back", config).lower()
    assert away.is_away(config) is False


# --- plain chat falls through to the LLM ----------------------------------

@pytest.mark.parametrize("text", [
    "Jarvis what's the weather like today",
    "tell me a joke",
    "what is the capital of France",
])
def test_plain_chat_is_not_intercepted(text):
    assert run(text) is None
