"""Provider failures classified, remembered, and never silently answered by the weak model."""
import asyncio

import pytest

from jarvis import llm
from jarvis import providers as pv
from jarvis.config import Config


class ProviderError(Exception):
    def __init__(self, status, message, headers=None):
        super().__init__(message)
        self.status_code = status
        self.response = type("R", (), {"status_code": status, "headers": headers or {}})()


@pytest.mark.parametrize("exc,kind", [
    (ProviderError(404, "The model `qwen/qwen3.6-27b` does not exist or you do not have access to it."), pv.MODEL_GONE),
    (ProviderError(400, "The model has been decommissioned"), pv.MODEL_GONE),
    (ProviderError(403, "Your project has been denied access. Please contact support."), pv.PERMISSION),
    (ProviderError(401, "Invalid API Key"), pv.AUTH),
    (ProviderError(429, "Rate limit reached for model"), pv.RATE_LIMIT),
    (ProviderError(503, "Service Unavailable"), pv.OUTAGE),
    (ConnectionError("Connection refused"), pv.OUTAGE),
    (TimeoutError("timed out"), pv.OUTAGE),
])
def test_failures_are_classified(exc, kind):
    assert pv.classify(exc).kind == kind


def test_keys_never_reach_the_failure_detail():
    f = pv.classify(ProviderError(401, "Invalid API Key: key=gsk_abcdefghijklmnop"))
    assert "gsk_" not in f.detail


def _p(name="gemini", model="gemini-3.6-flash", quality="strong"):
    return pv.Provider(name, "https://x", "k", model, quality)


def test_persistent_403_is_paused_for_hours_not_retried():
    p = _p()
    cooldown = pv.record_failure(p, pv.Failure(pv.PERMISSION, "denied"), now=1000.0)
    assert cooldown >= 3600
    assert pv.blocked(p, now=1000.0 + 1800)["kind"] == pv.PERMISSION
    assert pv.blocked(p, now=1000.0 + cooldown + 1) is None


def test_rate_limit_honours_retry_after():
    p = _p("groq", "m")
    assert pv.record_failure(p, pv.Failure(pv.RATE_LIMIT, "slow down", retry_after=7), now=0) == 7


def test_outages_back_off_and_success_resets():
    p = _p("groq", "m")
    first = pv.record_failure(p, pv.Failure(pv.OUTAGE, "502"), now=0)
    second = pv.record_failure(p, pv.Failure(pv.OUTAGE, "502"), now=0)
    assert second == 2 * first and second <= pv.MAX_OUTAGE_COOLDOWN_S
    pv.record_success(p)
    assert pv.blocked(p, now=1) is None


def test_a_new_model_name_is_not_blocked_by_the_old_ones_failure():
    pv.record_failure(_p("groq", "qwen/qwen3.6-27b"), pv.Failure(pv.MODEL_GONE, "gone"))
    assert pv.blocked(_p("groq", "qwen/qwen3.8-27b")) is None


# --------------------------------------------------------------------------- routing

def _config():
    c = Config()
    c.brain, c.groq_api_key, c.gemini_api_key = "ollama", "g", "m"
    c.groq_model, c.gemini_model, c.ollama_model = "qwen/qwen3.8-27b", "gemini-3.6-flash", "qwen2.5:3b"
    return c


def test_gemini_403_is_asked_once_then_skipped(monkeypatch):
    monkeypatch.delenv("JARVIS_STRONG_MODEL", raising=False)
    asked = []

    def ask(p, *a):
        asked.append(p.id)
        if p.name == "gemini":
            raise ProviderError(403, "Your project has been denied access.")
        if p.name == "groq":
            raise ProviderError(503, "down")
        return "local answer"

    c = _config()
    first = llm.complete_sync("s", "q", c, strength="strong", ask=ask)
    assert "gemini:gemini-3.6-flash" in asked
    asked.clear()
    pv.reset(pv.Provider("groq", pv.GROQ_URL, "g", "qwen/qwen3.8-27b", "strong"))
    second = llm.complete_sync("s", "q", c, strength="strong", ask=ask)
    assert "gemini:gemini-3.6-flash" not in asked          # the breaker held
    assert any("permission_denied (paused)" in f for f in second.failures)
    assert not first.ok and not second.ok


def test_study_never_silently_gets_the_weak_model(monkeypatch):
    monkeypatch.delenv("JARVIS_ALLOW_WEAK_TEACHING", raising=False)
    asked = []

    def ask(p, *a):
        asked.append(p.name)
        if p.quality == "strong":
            raise ProviderError(403, "denied")
        return "the sum of the sides"                       # what the 3B model actually said

    out = llm.complete_sync("s", "q", _config(), strength="strong", ask=ask)
    assert not out.ok and out.weak_only and "ollama" not in asked
    assert "Only the local model is available" in out.unavailable_message()


def test_weak_teaching_is_opt_in_and_labelled(monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOW_WEAK_TEACHING", "1")

    def ask(p, *a):
        if p.quality == "strong":
            raise ProviderError(403, "denied")
        return "an answer"
    out = llm.complete_sync("s", "q", _config(), strength="strong", ask=ask)
    assert out.ok and out.quality == "weak"


def test_everyday_completion_still_uses_the_configured_brain():
    out = llm.complete_sync("s", "q", _config(), ask=lambda p, *a: f"from {p.name}")
    assert out.text == "from ollama"


def test_video_says_so_and_reads_the_transcript_when_no_strong_model(monkeypatch):
    from jarvis import video_command as vc
    from jarvis.screen import page as pg
    from tests.test_screen_youtube import FakePage

    monkeypatch.setattr(pg, "active_page", lambda: FakePage(time=80.0))

    def ask(p, *a):
        if p.quality == "strong":
            raise ProviderError(403, "denied")
        raise AssertionError("the weak model must not be asked to teach")
    monkeypatch.setattr(llm, "_ask", ask)
    monkeypatch.delenv("JARVIS_ALLOW_WEAK_TEACHING", raising=False)
    reply = asyncio.run(vc.handle("explain what he just said", _config()))
    assert reply.startswith("Only the local model is available")
    assert "Here's what was said:" in reply and "a squared plus b squared equals c squared" in reply


# --------------------------------------------------------------------------- health check

class FakeClient:
    def __init__(self, models, generate_error=None, list_error=None):
        self._models, self._gen, self._list = models, generate_error, list_error
        self.models = self
        self.chat = type("C", (), {"completions": self})()

    def list(self):
        if self._list:
            raise self._list
        return type("L", (), {"data": [type("M", (), {"id": m})() for m in self._models]})()

    def create(self, **kw):
        if self._gen:
            raise self._gen
        return None


def test_health_check_finds_a_retired_model_and_suggests_current_ones():
    p = _p("groq", "qwen/qwen3.6-27b")
    h = pv.check(p, client=FakeClient(["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "whisper-large-v3"]))
    assert not h.ok and h.kind == pv.MODEL_GONE
    assert "qwen/qwen3.8-27b" in h.line() and "whisper" not in h.line()
    assert pv.blocked(p)["kind"] == pv.MODEL_GONE


def test_health_check_catches_a_403_that_the_model_list_hides():
    p = _p()
    h = pv.check(p, client=FakeClient(["models/gemini-3.6-flash"], generate_error=ProviderError(403, "denied access")))
    assert not h.ok and h.kind == pv.PERMISSION
    assert "denied access" in h.line()


def test_health_check_ok_clears_the_breaker():
    p = _p("groq", "qwen/qwen3.8-27b")
    pv.record_failure(p, pv.Failure(pv.OUTAGE, "x"))
    assert pv.check(p, client=FakeClient(["qwen/qwen3.8-27b"])).ok
    assert pv.blocked(p) is None


def test_summary_warns_when_no_strong_model():
    weak = pv.Health(_p("ollama", "qwen2.5:3b", "weak"), True)
    bad = pv.Health(_p(), False, pv.PERMISSION, "denied")
    assert "No strong model is available" in pv.summary([weak, bad])
    assert "No strong model" not in pv.summary([pv.Health(_p("groq", "m"), True), weak])


# --------------------------------------------------------------------------- nothing needs a model

@pytest.mark.parametrize("said", [
    "Message Papa on WhatsApp: I'll be home by eight",
    "don't announce instagram for two hours",
    "summarise my notifications",
    "go to sleep",
    "wake up",
])
def test_deterministic_commands_work_with_every_provider_down(said, monkeypatch, tmp_path):
    from jarvis import commands
    from jarvis.integrations import contacts, phone_contacts, whatsapp

    monkeypatch.setattr(llm, "_ask", lambda *a, **k: (_ for _ in ()).throw(ProviderError(503, "down")))
    monkeypatch.setattr(contacts, "_load", lambda: {})
    monkeypatch.setattr(phone_contacts, "all_contacts", lambda: [{"name": "Papa", "number": "+919810000001"}])
    monkeypatch.setattr(whatsapp, "resolve", lambda name: [])
    monkeypatch.setenv("JARVIS_DRY_RUN_SENDS", "1")
    monkeypatch.setattr("jarvis.config.CONFIG.vault_path", tmp_path)
    reply = asyncio.run(commands.handle(said, Config()))
    assert reply and "model" not in reply.lower()
    from jarvis import power
    power.set_asleep(False)
