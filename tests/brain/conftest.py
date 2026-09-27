"""Isolation for Daily Brain tests: temporary state and settings, memory keys, a fake network.

No test here can touch the real provider-health file, brain.json, the keyring or the internet:
every httpx request made through the brain's adapters goes to ``FakeProviders``, and anything
the fake does not recognise fails the test.
"""
import pytest

from brain_fakes import FakeProviders, fake_config


@pytest.fixture(autouse=True)
def _isolated_brain(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JARVIS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("JARVIS_BRAIN_CONFIG", str(tmp_path / "config" / "brain.json"))
    monkeypatch.setenv("JARVIS_KEY_BACKEND", "memory")
    monkeypatch.setenv("JARVIS_DAILY_BRAIN", "1")
    monkeypatch.delenv("JARVIS_ALLOW_WEAK_TEACHING", raising=False)
    from jarvis.brain import adapters, executor
    executor.mark_online()
    fake = FakeProviders()
    monkeypatch.setitem(adapters.TRANSPORT, "transport", fake.transport())
    yield fake
    executor.mark_online()


@pytest.fixture
def fake(_isolated_brain):
    return _isolated_brain


@pytest.fixture
def brain_env(fake, monkeypatch):
    """A registry + key store over fictional Groq, Gemini and Ollama, with tools/vision verified."""
    from jarvis.brain.keys import KeyStore, MemoryBackend
    from jarvis.brain.registry import BrainSettings, BrainState, Registry
    from brain_fakes import FAKE_GEMINI_KEY, FAKE_GROQ_KEY

    env = {"GROQ_API_KEY": FAKE_GROQ_KEY, "GEMINI_API_KEY": FAKE_GEMINI_KEY}

    def build(config=None, verify=True, settings=None):
        cfg = config or fake_config()
        st = BrainState()
        if verify:
            for ref in ("groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b", "gemini/gemini-3.6-flash"):
                st.verify(ref, "tool_calling", True, "5/5")
            st.verify("gemini/gemini-3.6-flash", "vision", True, "1/1")
        reg = Registry(cfg, settings or BrainSettings(), st)
        ks = KeyStore(reg.settings, st, MemoryBackend(), env=env)
        return reg, ks

    return build
