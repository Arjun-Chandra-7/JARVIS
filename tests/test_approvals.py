"""One approval system for every side effect: propose, then yes / cancel on a later turn."""
import asyncio
import json

import pytest

from jarvis import approvals
from jarvis.approvals import ApprovalManager


@pytest.fixture
def mgr(tmp_path):
    return ApprovalManager(ttl_s=60, audit_path=tmp_path / "approvals.jsonl")


def _run(coro):
    return asyncio.run(coro)


def _spy(label, log, result=None):
    def execute():
        log.append(label)
        return result if result is not None else {"ok": True, "message": f"{label} done."}
    return execute


def _message(mgr, log, who="Papa", body="I'll be home by eight", **kw):
    return mgr.propose("message", f'send a WhatsApp to {who}: "{body}"',
                       {"recipient": who, "platform": "WhatsApp", "action": "send", "message": body},
                       _spy(f"message:{who}", log), **kw)


def _email(mgr, log, to="teacher@school.example"):
    return mgr.propose("email", f'send an email to {to} with the subject "Leave"',
                       {"recipient": to, "platform": "Gmail", "action": "send email", "body": "secret body"},
                       _spy(f"email:{to}", log))


# --------------------------------------------------------------------------- the basic yes / no

@pytest.mark.parametrize("said", ["yes", "send it", "confirm", "Jarvis, yes please", "go ahead",
                                  "haan", "haan bhej do", "bhej do", "haan ji", "theek hai kar do", "kar do"])
def test_voice_approval_runs_the_action(mgr, said):
    log = []
    action = _message(mgr, log)
    assert "to confirm" in action.prompt()
    out = _run(mgr.answer(said))
    assert out.status == "executed", out.message
    assert log == ["message:Papa"] and out.message == "message:Papa done."
    assert mgr.pending() == []


@pytest.mark.parametrize("said", ["cancel", "no", "don't send it", "nahi", "mat bhejo", "rehne do",
                                  "nhi rehne de", "never mind"])
def test_voice_cancellation(mgr, said):
    log = []
    _message(mgr, log)
    out = _run(mgr.answer(said))
    assert out.status == "cancelled" and log == []
    assert "won't send a WhatsApp to Papa" in out.message
    assert _run(mgr.answer("yes")) is None          # nothing left to approve


def test_bare_ok_approves_nothing(mgr):
    log = []
    _message(mgr, log)
    assert _run(mgr.answer("ok")) is None and log == []


def test_an_unrelated_sentence_is_not_an_answer(mgr):
    log = []
    _message(mgr, log)
    for said in ["stop reading notifications", "no idea what the capital of Peru is", "yes the weather is nice today"]:
        assert _run(mgr.answer(said)) is None, said
    assert log == [] and len(mgr.pending()) == 1


def test_nothing_pending_means_yes_is_not_an_answer(mgr):
    assert _run(mgr.answer("yes")) is None


# --------------------------------------------------------------------------- expiry

def test_expired_approval_cannot_execute(mgr, monkeypatch):
    log = []
    action = _message(mgr, log, ttl_s=5)
    real = approvals.time.time
    monkeypatch.setattr(approvals.time, "time", lambda: real() + 6)
    out = _run(mgr.answer("yes"))
    assert out.status == "expired" and "expired" in out.message and log == []
    assert _run(mgr.confirm(action.id)).status in {"expired", "none"}
    assert log == []


# --------------------------------------------------------------------------- the right action

def test_one_approval_cannot_authorise_a_different_action(mgr):
    log = []
    a = _message(mgr, log, who="Papa")
    b = _email(mgr, log)
    out = _run(mgr.confirm(a.id))
    assert out.ok and log == ["message:Papa"]
    assert [p.id for p in mgr.pending()] == [b.id]
    # And a stale approval token for A cannot be replayed against B.
    assert _run(mgr.confirm(b.id, fingerprint=a.fingerprint())).status == "none"
    assert log == ["message:Papa"]


def test_ambiguous_yes_approves_nothing_and_asks(mgr):
    log = []
    _message(mgr, log)
    _email(mgr, log)
    out = _run(mgr.answer("yes"))
    assert out.status == "ambiguous" and log == []
    assert "Which one" in out.message and "message" in out.message and "email" in out.message
    assert len(mgr.pending()) == 2


def test_naming_the_action_picks_it(mgr):
    log = []
    _message(mgr, log)
    _email(mgr, log)
    assert _run(mgr.answer("send the email")).ok
    assert log == ["email:teacher@school.example"]
    assert _run(mgr.answer("cancel the message")).status == "cancelled"
    assert log == ["email:teacher@school.example"] and mgr.pending() == []


def test_naming_the_recipient_picks_it(mgr):
    log = []
    _message(mgr, log, who="Papa")
    _message(mgr, log, who="Mummy", body="dinner?")
    assert _run(mgr.answer("haan, send it to Mummy")).ok
    assert log == ["message:Mummy"]


def test_yes_to_something_not_pending_approves_nothing(mgr):
    log = []
    _message(mgr, log)
    out = _run(mgr.answer("yes send the email"))
    assert out.status == "ambiguous" and log == []


def test_ordinal_picks_in_order(mgr):
    log = []
    _message(mgr, log)
    _email(mgr, log)
    assert _run(mgr.answer("yes the second one")).ok
    assert log == ["email:teacher@school.example"]


def test_sessions_do_not_approve_each_other(mgr):
    log = []
    _message(mgr, log, session="phone")
    assert _run(mgr.answer("yes", session="local")) is None and log == []
    assert _run(mgr.answer("yes", session="phone")).ok


# --------------------------------------------------------------------------- results

def test_provider_failure_after_confirmation_is_reported(mgr):
    def boom():
        raise RuntimeError("HttpError 403: insufficient permission for gmail.send")
    mgr.propose("email", "send an email to x@y.example", {"recipient": "x@y.example"}, boom)
    out = _run(mgr.answer("yes"))
    assert out.status == "failed"
    assert "HttpError 403" in out.message and "send an email to x@y.example" in out.message


def test_provider_saying_no_is_a_failure(mgr):
    mgr.propose("message", "send a WhatsApp to Papa", {"recipient": "Papa"},
                lambda: {"ok": False, "message": "Couldn't send to Papa: +91… is not on WhatsApp"})
    out = _run(mgr.answer("send it"))
    assert out.status == "failed" and "not on WhatsApp" in out.message


def test_async_execute_is_awaited(mgr):
    async def run():
        return "Created 'Physics test'."
    mgr.propose("calendar", 'add "Physics test"', {"title": "Physics test"}, run)
    assert _run(mgr.answer("yes")).message == "Created 'Physics test'."


def test_audit_has_no_bodies_or_secrets(mgr, tmp_path):
    log = []
    _message(mgr, log, body="the wifi password is hunter2")
    _run(mgr.answer("yes"))
    _email(mgr, log)
    _run(mgr.answer("cancel"))
    text = (tmp_path / "approvals.jsonl").read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    assert [r["event"] for r in rows] == ["proposed", "confirmed", "executed", "proposed", "cancelled"]
    assert "hunter2" not in text and "secret body" not in text and "teacher@" not in text


def test_terminal_answers_inline_through_the_same_path(mgr):
    log = []

    async def ask(summary):
        return "Papa" in summary
    out = _run(mgr.propose_or_ask("message", "send a WhatsApp to Papa", {"recipient": "Papa"},
                                  _spy("message:Papa", log), ask=ask))
    assert out.ok and log == ["message:Papa"] and mgr.pending() == []


# --------------------------------------------------------------------------- the tools, end to end

def _registry(confirm=None):
    from jarvis.agent.groq_tools import build_registry
    from jarvis.config import Config
    config = Config()
    config.allow_unconfirmed_shell = False
    return config, build_registry(config, job_runner=None, confirm_fn=confirm)[1]


def test_web_voice_email_is_proposed_not_refused(monkeypatch, tmp_path):
    """The web agent has no confirm_fn. Email used to answer "user declined"; now it waits."""
    from jarvis import commands
    from jarvis.approvals import MANAGER
    from jarvis.config import CONFIG
    from jarvis.integrations.google import gmail

    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(gmail, "send", lambda config, to, subject, body: sent.append((to, subject, body))
                        or f"Sent to {to} (id abc).")
    _, dispatch = _registry(None)
    reply = _run(dispatch("google_email_send", {"to": "teacher@school.example", "subject": "Leave",
                                                "body": "I was ill."}))
    assert "declined" not in reply and "to confirm" in reply and not sent
    assert len(MANAGER.pending()) == 1
    answer = _run(commands.handle("haan bhej do", CONFIG))
    assert answer == "Sent to teacher@school.example (id abc)."
    assert sent == [("teacher@school.example", "Leave", "I was ill.")]


def test_calendar_not_connected_is_reported_after_yes(monkeypatch, tmp_path):
    from jarvis.integrations.google import calendar as gcal

    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(gcal, "create_event", lambda *a, **k: None)
    _, dispatch = _registry(None)
    _run(dispatch("google_calendar_create", {"title": "Physics test", "start": "2026-09-25T10:00:00",
                                             "end": "2026-09-25T11:00:00"}))
    out = _run(approvals.MANAGER.answer("yes"))
    assert out.status == "failed" and "isn't connected" in out.message


def test_destructive_shell_waits_for_yes(monkeypatch, tmp_path):
    from jarvis.agent import sandbox
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    ran = []
    monkeypatch.setattr(sandbox, "run", lambda cmd, policy, cwd, timeout: ran.append(cmd)
                        or sandbox.Result(stdout="removed", returncode=0, sandboxed=True))
    _, dispatch = _registry(None)
    reply = _run(dispatch("run_bash", {"command": "rm -rf ~/scratch/old"}))
    assert "to confirm" in reply and ran == []
    assert _run(approvals.MANAGER.answer("run it")).message == "removed"
    assert ran == ["rm -rf ~/scratch/old"]


def test_overlay_route_confirms_by_id_and_fingerprint(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from jarvis import webserver
    from jarvis.approvals import MANAGER

    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(webserver.hud_state, "log_turn", lambda *a: None)
    log = []
    a = _message(MANAGER, log)
    b = _email(MANAGER, log)
    client = TestClient(webserver.app)
    listed = client.get("/approvals").json()["pending"]
    assert [p["id"] for p in listed] == [a.id, b.id]
    assert "secret body" not in str(listed)
    # A fingerprint from A does not unlock B.
    r = client.post(f"/approvals/{b.id}", json={"decision": "confirm", "fingerprint": a.fingerprint()})
    assert r.json()["status"] == "none" and log == []
    r = client.post(f"/approvals/{a.id}", json={"decision": "confirm", "fingerprint": a.fingerprint()})
    assert r.json()["status"] == "executed" and log == ["message:Papa"]
    r = client.post(f"/approvals/{b.id}", json={"decision": "cancel"})
    assert r.json()["status"] == "cancelled" and log == ["message:Papa"]
