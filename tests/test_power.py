"""Soft on/off (sleep) — state persistence and command routing."""
import asyncio

import pytest

from jarvis import commands, power
from jarvis.config import Config


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    from jarvis.integrations import coding_jobs
    monkeypatch.setattr(coding_jobs, "_DEFAULT_MANAGER",
                        coding_jobs.CodingJobManager(state_path=tmp_path / "cj.json"))
    yield


def run(text):
    return asyncio.run(commands.handle(text, Config()))


def test_state_roundtrip_and_persist():
    assert power.asleep() is False and power.since() == 0.0
    power.set_asleep(True)
    assert power.asleep() is True and power.since() > 0
    power.set_asleep(False)
    assert power.asleep() is False and power.since() == 0.0


@pytest.mark.parametrize("phrase", [
    "Jarvis go to sleep", "Jarvis goodnight", "Jarvis stand down",
    "Jarvis power down", "Jarvis that's all", "Jarvis sleep mode",
])
def test_sleep_phrases(phrase):
    assert "sleep" in run(phrase).lower()
    assert power.asleep() is True


@pytest.mark.parametrize("phrase", [
    "Jarvis wake up", "Jarvis come back", "Jarvis I need you", "Jarvis reactivate",
])
def test_wake_phrases(phrase):
    power.set_asleep(True)
    assert "online" in run(phrase).lower()
    assert power.asleep() is False


def test_asleep_ignores_other_commands():
    power.set_asleep(True)
    reply = run("Jarvis what's the weather")
    assert "asleep" in reply.lower()
    assert power.asleep() is True                       # still asleep — command not run


def test_wake_wins_even_while_asleep():
    power.set_asleep(True)
    assert "online" in run("Jarvis wake up").lower()
    assert power.asleep() is False


def test_notifications_command_still_works_when_awake():
    assert "off" in run("Jarvis turn off notifications").lower()
