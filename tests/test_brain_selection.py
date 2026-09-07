"""Brain selection + local Ollama rate-limit fallback."""
import os
from types import SimpleNamespace

import pytest

from jarvis.agent.factory import make_agent
from jarvis.config import Config


@pytest.mark.parametrize("brain,host,model_env", [
    ("gemini", "generativelanguage.googleapis.com", "gemini"),
    ("groq", "api.groq.com", "groq"),
    ("ollama", "localhost:11434", "ollama"),
])
def test_llm_params_route_per_brain(monkeypatch, brain, host, model_env):
    monkeypatch.setenv("JARVIS_BRAIN", brain)
    base, key, model = Config().llm_params()
    assert host in base
    if brain == "ollama":
        assert base.endswith("/v1") and key == "ollama"


def test_factory_accepts_ollama(monkeypatch):
    monkeypatch.setenv("JARVIS_BRAIN", "ollama")
    agent = make_agent(Config(), mode="text")
    assert agent.__class__.__name__ == "GroqAgent"


def test_factory_rejects_unknown_brain(monkeypatch):
    monkeypatch.setenv("JARVIS_BRAIN", "hal9000")
    with pytest.raises(SystemExit):
        make_agent(Config(), mode="text")


def test_local_fallback_repoints_client_when_ollama_reachable(monkeypatch):
    import httpx

    monkeypatch.setenv("JARVIS_BRAIN", "groq")
    agent = make_agent(Config(), mode="text")
    assert agent._on_local is False

    class _Resp:
        def raise_for_status(self): pass
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    assert agent._try_local_fallback() is True
    assert agent._on_local is True
    assert agent.model == Config().ollama_model
    assert agent._try_local_fallback() is False        # only switch once


def test_local_fallback_is_skipped_when_ollama_down(monkeypatch):
    import httpx

    monkeypatch.setenv("JARVIS_BRAIN", "groq")
    agent = make_agent(Config(), mode="text")

    def _boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(httpx, "get", _boom)
    assert agent._try_local_fallback() is False
    assert agent._on_local is False
