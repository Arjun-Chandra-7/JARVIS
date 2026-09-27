"""Cross-feature tests: Daily Brain + Study Companion + 3D Studio + the rest of JARVIS together.

Same isolation as tests/brain (fake provider server, fictional keys, temporary state), with the
Daily Brain on. Nothing here reaches a real model, the real screen, the real overlay, WhatsApp,
Blender's real projects folder or the owner's files.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

from brain_fakes import FAKE_GEMINI_KEY, FAKE_GROQ_KEY, FakeProviders, fake_config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JARVIS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("JARVIS_BRAIN_CONFIG", str(tmp_path / "config" / "brain.json"))
    monkeypatch.setenv("JARVIS_STUDY_DIR", str(tmp_path / "study"))
    monkeypatch.setenv("JARVIS_3D_ROOT", str(tmp_path / "3d"))
    monkeypatch.setenv("JARVIS_KEY_BACKEND", "memory")
    monkeypatch.setenv("JARVIS_DAILY_BRAIN", "1")
    monkeypatch.delenv("JARVIS_STUDY_COMPANION", raising=False)
    monkeypatch.delenv("JARVIS_ALLOW_WEAK_TEACHING", raising=False)
    from jarvis.brain import adapters, executor
    executor.mark_online()
    fake = FakeProviders()
    monkeypatch.setitem(adapters.TRANSPORT, "transport", fake.transport())
    yield fake
    executor.mark_online()


@pytest.fixture
def fake(_isolated):
    return _isolated


@pytest.fixture
def db(fake, monkeypatch):
    """A DailyBrain over fictional Groq, Gemini and Ollama, tools/vision verified, installed as the
    process's brain so capability requests from other subsystems use it too."""
    from jarvis.brain import daily
    from jarvis.brain.keys import KeyStore, MemoryBackend
    from jarvis.brain.registry import BrainSettings, BrainState, Registry

    st = BrainState()
    for ref in ("groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b", "gemini/gemini-3.6-flash"):
        st.verify(ref, "tool_calling", True, "5/5")
    st.verify("gemini/gemini-3.6-flash", "vision", True, "1/1")
    st.verify("ollama/moondream", "vision", True, "1/1")
    reg = Registry(fake_config(), BrainSettings(), st)
    ks = KeyStore(reg.settings, st, MemoryBackend(),
                  env={"GROQ_API_KEY": FAKE_GROQ_KEY, "GEMINI_API_KEY": FAKE_GEMINI_KEY})
    b = daily.DailyBrain(fake_config(), reg, ks, searcher=lambda q: [], recall=lambda q: "")
    monkeypatch.setitem(daily._BRAIN, "b", b)
    return b


class FakeTransport:
    """The teach bus's transport, recording batches instead of posting to the overlay."""

    def __init__(self):
        self.posts = []

    def post(self, kind, text):
        self.posts.append((kind, text))
        return True


@pytest.fixture
def overlay():
    from jarvis.teach.bus import Overlay
    t = FakeTransport()
    ov = Overlay(transport=t)
    ov.work_area = lambda monitor="primary": {"x": 0, "y": 0, "w": 1920, "h": 1080}
    ov.transport_log = t
    return ov


@pytest.fixture
def study(overlay, monkeypatch):
    """The live companion with the real Brain adapter, a recording overlay and a screen that is
    whatever the test says it is."""
    from jarvis import screen_safety, study_live
    from jarvis.study.context import FixtureContextProvider

    safe = {"verdict": screen_safety.Verdict(True)}
    renderer = study_live.OverlayRenderer(overlay=overlay, safety=lambda: safe["verdict"])
    provider = FixtureContextProvider()
    study_live.use(renderer=renderer, provider=provider)
    comp = study_live.companion()
    return type("Study", (), {"comp": comp, "renderer": renderer, "provider": provider, "safe": safe,
                              "overlay": overlay, "live": study_live})
