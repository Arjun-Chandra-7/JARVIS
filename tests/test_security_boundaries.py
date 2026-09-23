"""Security boundaries found in the audit: a model that could lift its own confirmation gate, a
second shell tool outside the sandbox, credential files reachable through the file tools, and a
local HTTP API readable from a DNS-rebound page."""
import asyncio
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis import mobile
from jarvis.agent import sandbox
from jarvis.config import Config


def _registry(confirm):
    from jarvis.agent.groq_tools import build_registry

    config = Config()
    config.allow_unconfirmed_shell = False
    _, dispatch = build_registry(config, job_runner=None, confirm_fn=confirm)
    return config, dispatch


def _call(dispatch, name, args):
    return asyncio.run(dispatch(name, args))


def test_model_cannot_lift_its_own_confirmation_gate():
    asked = []

    async def deny(desc):
        asked.append(desc)
        return False

    config, dispatch = _registry(deny)
    out = _call(dispatch, "enable_full_laptop_autonomy", {"enable": True})
    assert asked, "enabling autonomy must ask the person"
    assert config.allow_unconfirmed_shell is False
    assert "stays off" in out


def test_autonomy_without_any_confirm_channel_stays_off():
    config, dispatch = _registry(None)
    _call(dispatch, "enable_full_laptop_autonomy", {"enable": True})
    assert config.allow_unconfirmed_shell is False


def test_autonomy_can_always_be_turned_back_off():
    async def never(desc):
        raise AssertionError("turning the gate back on should not need a confirmation")

    config, dispatch = _registry(never)
    config.allow_unconfirmed_shell = True
    _call(dispatch, "enable_full_laptop_autonomy", {"enable": False})
    assert config.allow_unconfirmed_shell is False


def test_full_control_shell_goes_through_the_same_gate_and_sandbox(monkeypatch):
    ran = []

    def fake_run(cmd, policy, cwd, timeout):
        ran.append((cmd, policy))
        return sandbox.Result(stdout="ok", returncode=0, sandboxed=True)

    monkeypatch.setattr(sandbox, "run", fake_run)

    async def deny(desc):
        return False

    _, dispatch = _registry(deny)
    assert "declined" in _call(dispatch, "control_laptop_full", {"command_or_script": "rm -rf ~/x"})
    assert not ran
    _call(dispatch, "control_laptop_full", {"command_or_script": "echo hi"})
    assert ran and ran[0][1] is sandbox.GUARDED


@pytest.mark.parametrize("path", [
    "~/.ssh/id_ed25519", "~/.ssh", "~/.gnupg/private-keys-v1.d/x.key", "~/project/.env",
    "~/project/.env.local", "~/.config/jarvis/mobile-token", "~/.aws/credentials",
])
def test_credential_paths_are_refused(path):
    assert sandbox.is_secret_path(path)


@pytest.mark.parametrize("path", ["~/notes/todo.md", "~/project/.env.example", "/tmp/x.txt"])
def test_ordinary_paths_are_allowed(path):
    assert not sandbox.is_secret_path(path)


def test_read_file_tool_refuses_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".ssh" / "id_rsa"
    key.parent.mkdir()
    key.write_text("PRIVATE")
    _, dispatch = _registry(None)
    out = _call(dispatch, "read_file", {"path": str(key)})
    assert "PRIVATE" not in out and "refused" in out


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(mobile, "_fail_times", {})
    app = FastAPI()
    app.middleware("http")(mobile.authorize)

    @app.get("/whatsapp/inbox")
    def inbox():
        return [{"text": "private"}]

    return TestClient(app)


def test_rebound_host_cannot_read_local_routes(client):
    r = client.get("/whatsapp/inbox", headers={"host": "attacker.example:8770"})
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8770", "localhost:8770", "[::1]:8770", "localhost"])
def test_local_host_headers_still_work(client, host):
    assert client.get("/whatsapp/inbox", headers={"host": host}).status_code == 200


def test_bridge_rejects_lookalike_origins_and_foreign_hosts():
    js = (Path(__file__).resolve().parents[1] / "whatsapp" / "wa_service.js").read_text()
    assert 'origin.startsWith("http://127.0.0.1")' not in js
    assert "TRUSTED_ORIGINS.has(origin)" in js and "trustedHost(req.headers.host)" in js


def test_bridge_never_names_a_chat_after_the_owner():
    js = (Path(__file__).resolve().parents[1] / "whatsapp" / "wa_service.js").read_text()
    assert "if (!m.key.fromMe) recordContact(m.key.remoteJid, m.pushName);" in js
    assert "(!m?.key?.fromMe && m?.pushName)" in js
