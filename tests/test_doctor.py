"""`jarvis doctor`: honest about what is degraded, silent about secrets, and incapable of side effects."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis import doctor


def test_it_contains_no_way_to_cause_a_side_effect():
    src = Path(doctor.__file__).read_text()
    # No POST, no request objects with a body, no sending, no typing, no recording.
    for forbidden in ('method="POST"', "data=", "urlopen(urllib.request.Request", "sendMessage",
                      "ydotool", "xdotool", "record_utterance", "arecord", "propose(", "activate("):
        assert forbidden not in src, forbidden


def test_one_failing_cloud_provider_is_degraded_not_down(monkeypatch):
    summary = ("groq · openai/gpt-oss-120b: ok (strong)\n"
               "gemini · gemini-3.6-flash: the account or project was denied access\n"
               "ollama · qwen2.5:3b: ok (weak)")
    monkeypatch.setattr(doctor, "_get", lambda url, timeout=3: (
        {"summary": summary, "strong_available": True} if url.endswith("/providers") else
        {"models": [{"name": "qwen2.5:3b"}]} if "11434" in url else None))
    local, cloud = doctor.check_models()
    assert local.status == doctor.READY
    assert cloud.status == doctor.DEGRADED and "gemini" in cloud.detail and "denied" in cloud.detail


def test_no_strong_model_and_no_local_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(doctor, "_get", lambda url, timeout=3: (
        {"summary": "groq · x: rate limited", "strong_available": False} if url.endswith("/providers") else None))
    local, cloud = doctor.check_models()
    assert local.status == doctor.DEGRADED and cloud.status == doctor.UNAVAILABLE


def test_key_values_never_appear_only_whether_they_are_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_THISISASECRETVALUE1234567890")
    monkeypatch.setattr(doctor, "_get", lambda url, timeout=3: (
        {"summary": "groq · m: ok (strong)", "strong_available": True} if url.endswith("/providers") else None))
    out = json.dumps([c.__dict__ for c in doctor.check_models()])
    assert "THISISASECRET" not in out and '"groq": true' in out


def test_a_missing_gpu_driver_asks_for_the_user_and_says_what_still_works(monkeypatch):
    monkeypatch.setattr(doctor, "_run", lambda argv, timeout=5: (
        "01:00.0 VGA compatible controller: NVIDIA Corporation" if argv[0] == "lspci" else
        "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver."))
    gpu = doctor.check_gpu()
    assert gpu.status == doctor.USER and "processor" in gpu.detail


def test_a_full_gpu_is_degraded(monkeypatch):
    monkeypatch.setattr(doctor, "_run", lambda argv, timeout=5: "NVIDIA" if argv[0] == "lspci" else "3900, 4096\n")
    assert doctor.check_gpu().status == doctor.DEGRADED


def test_the_backend_down_makes_the_installation_unavailable(monkeypatch):
    monkeypatch.setattr(doctor, "_get", lambda url, timeout=3: None)
    monkeypatch.setattr(doctor, "_unit", lambda unit: {"LoadState": "loaded", "ActiveState": "failed",
                                                       "SubState": "failed", "MainPID": "0", "NRestarts": "5"})
    monkeypatch.setattr(doctor, "_run", lambda argv, timeout=5: "")
    report = doctor.run_all()
    assert report["overall"] == doctor.UNAVAILABLE
    backend = next(c for c in report["checks"] if c["name"] == "jarvis-backend")
    assert backend["status"] == doctor.UNAVAILABLE
    whatsapp = next(c for c in report["checks"] if c["name"] == "jarvis-whatsapp")
    assert whatsapp["status"] == doctor.DEGRADED          # messaging down is degraded, not "Jarvis is down"


def test_a_service_on_an_old_commit_is_flagged(monkeypatch):
    monkeypatch.setattr(doctor, "_get", lambda url, timeout=3: (
        {"build": {"commit": "5647adc00000"}, "voice_build": {"commit": "c4e5ec5e66e0"}} if url.endswith("/health")
        else {"commit": "c4e5ec5e66e0"}))
    monkeypatch.setattr(doctor, "_unit", lambda unit: {"LoadState": "loaded", "ActiveState": "active",
                                                       "MainPID": "1", "NRestarts": "0"})
    checks = {c.name: c for c in doctor.check_services("c4e5ec5e66e0aaaa")}
    assert checks["jarvis-backend"].status == doctor.DEGRADED and "restart" in checks["jarvis-backend"].detail
    assert checks["jarvis-voice"].status == doctor.READY


def test_the_human_summary_names_every_check():
    report = {"overall": "degraded", "commit": "abc", "took_s": 1.0,
              "checks": [{"name": "x", "status": "ready", "detail": "fine"},
                         {"name": "y", "status": "degraded", "detail": "slow"}]}
    text = doctor.summary(report)
    assert re.search(r"✅ x\s+fine", text) and re.search(r"🟡 y\s+slow", text)


@pytest.mark.parametrize("muted, status", [(True, doctor.USER), (False, doctor.READY), (None, doctor.DEGRADED)])
def test_a_muted_microphone_is_named_not_called_ready(monkeypatch, muted, status):
    """Muted for a day and a half while every other check here said ready."""
    from jarvis.audio import inputs

    monkeypatch.setattr(inputs, "is_muted", lambda: muted)
    monkeypatch.setattr(inputs, "unmute", lambda: pytest.fail("doctor must not change anything"))
    assert doctor.check_microphone().status == status


def test_a_release_waiting_to_be_taken_is_not_called_ready(monkeypatch):
    """Fixes pushed to the release do nothing until a restart takes them; a reboot does not."""
    from jarvis import deploy

    waiting = deploy.State("update", head="87a0979b8197aaaa", behind=5,
                           message="The release is 5 commit(s) ahead; it can be fast-forwarded.")
    monkeypatch.setattr(deploy, "classify", lambda *a, **k: waiting)
    check = doctor.check_deploy()
    assert check.status == doctor.DEGRADED
    assert "5 commit(s) behind" in check.detail and "jarvis restart" in check.detail


def test_running_the_release_is_ready(monkeypatch):
    from jarvis import deploy

    monkeypatch.setattr(deploy, "classify", lambda *a, **k: deploy.State("release", head="2bdcc16", message="Running the release."))
    assert doctor.check_deploy().status == doctor.READY
