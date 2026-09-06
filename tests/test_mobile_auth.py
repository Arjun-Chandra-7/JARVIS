"""Milestone E: mobile API auth boundary + desktop-control request validation."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis import mobile


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(mobile, "_fail_times", {})
    app = FastAPI()
    app.middleware("http")(mobile.authorize)
    app.include_router(mobile.router)

    @app.get("/health")
    def health():                     # a non-/mobile route to test the local exemption
        return {"ok": True}

    return TestClient(app)


TOKEN = "s3cr3t-pair-token-value"


def _auth(t=TOKEN):
    return {"Authorization": f"Bearer {t}"}


def test_mobile_route_denied_without_token(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    assert client.get("/mobile/status").status_code == 401


def test_mobile_route_denied_with_wrong_token(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    assert client.get("/mobile/status", headers=_auth("nope")).status_code == 401


def test_mobile_route_allowed_with_token(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    r = client.get("/mobile/status", headers=_auth())
    assert r.status_code == 200 and r.json()["ok"] is True


def test_fail_closed_when_no_token_configured(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: "")
    assert client.get("/mobile/status", headers=_auth("anything")).status_code == 401


def test_bearer_prefix_is_case_insensitive(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    assert client.get("/mobile/status", headers={"Authorization": f"bearer {TOKEN}"}).status_code == 200


def test_control_requires_token_and_is_not_executed(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    called = []
    from jarvis.integrations import desktop_control
    monkeypatch.setattr(desktop_control, "available", lambda: called.append(1) or "ydotool")
    assert client.post("/mobile/control", json={"action": "type", "text": "rm -rf"}).status_code == 401
    assert called == []                                   # never reached the handler


def test_non_mobile_local_get_is_open_but_forwarded_is_gated(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    assert client.get("/health").status_code == 200
    assert client.get("/health", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 401


def test_repeated_failures_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    codes = [client.get("/mobile/status", headers=_auth("bad")).status_code for _ in range(12)]
    assert codes[-1] == 429 and 401 in codes


def test_control_validates_action_and_accepts_relative_move(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    from jarvis.integrations import desktop_control
    seen = {}
    monkeypatch.setattr(desktop_control, "available", lambda: "ydotool")
    monkeypatch.setattr(desktop_control, "move_rel", lambda dx, dy: seen.update(dx=dx, dy=dy) or True)
    assert client.post("/mobile/control", headers=_auth(), json={"action": "fly"}).status_code == 422
    r = client.post("/mobile/control", headers=_auth(), json={"action": "move", "dx": 12, "dy": -5})
    assert r.status_code == 200 and seen == {"dx": 12, "dy": -5}


def test_transcribe_rejects_odd_length_pcm(client, monkeypatch):
    monkeypatch.setattr(mobile, "token", lambda: TOKEN)
    r = client.post("/mobile/transcribe", headers=_auth(), content=b"\x01\x02\x03")
    assert r.status_code == 400
