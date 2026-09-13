"""Screen description routing: the small local model captions, the brain answers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.vision import analyze

GOOD = ("The image shows a code editor with several open tabs and a terminal panel "
        "reporting a failed test run.")


@pytest.fixture
def local_config():
    return SimpleNamespace(vision_provider="ollama", ollama_vision_model="moondream",
                           gemini_api_key="")


@pytest.fixture
def gemini_config():
    return SimpleNamespace(vision_provider="gemini", ollama_vision_model="moondream",
                           gemini_api_key="key")


@pytest.fixture(autouse=True)
def ollama_present(monkeypatch):
    monkeypatch.setattr(analyze, "_ollama_up", lambda _m: True)


def test_degenerate_detection():
    # "xtr" is a real observed reply to "What application is open?".
    assert analyze._looks_degenerate("xtr")
    assert analyze._looks_degenerate("")
    assert analyze._looks_degenerate(None)
    assert analyze._looks_degenerate("(vision error: timeout)")
    assert not analyze._looks_degenerate(GOOD)


def test_no_question_asks_for_a_plain_description(monkeypatch, local_config):
    seen = []
    monkeypatch.setattr(analyze, "_ollama", lambda p, q, m: seen.append(q) or GOOD)
    analyze.describe("/tmp/x.jpg", "", local_config)
    assert seen == [analyze._DEFAULT_Q]


def test_a_question_is_folded_into_the_description_prompt(monkeypatch, local_config):
    seen = []

    def fake(_p, q, _m):
        seen.append(q)
        return GOOD

    monkeypatch.setattr(analyze, "_ollama", fake)
    out = analyze.describe("/tmp/x.jpg", "is the build failing?", local_config)
    assert "is the build failing?" in seen[0]
    assert out == GOOD


def test_a_degenerate_answer_falls_back_to_the_description(monkeypatch, local_config):
    calls = []

    def fake(_p, q, _m):
        calls.append(q)
        return "xtr" if "Pay particular attention" in q else GOOD

    monkeypatch.setattr(analyze, "_ollama", fake)
    out = analyze.describe("/tmp/x.jpg", "what app is open?", local_config)
    assert len(calls) == 2
    assert GOOD in out
    assert "what app is open?" in out          # the brain is told what to answer


def test_both_attempts_degenerate_returns_something_not_none(monkeypatch, local_config):
    monkeypatch.setattr(analyze, "_ollama", lambda _p, _q, _m: "xtr")
    assert analyze.describe("/tmp/x.jpg", "what app?", local_config) == "xtr"


def test_gemini_gets_the_question_verbatim(monkeypatch, gemini_config):
    seen = []
    monkeypatch.setattr(analyze, "_gemini", lambda p, q, k: seen.append(q) or "an answer")
    analyze.describe("/tmp/x.jpg", "what app is open?", gemini_config)
    assert seen == ["what app is open?"]       # a capable VQA model needs no scaffolding


def test_no_backend_returns_none(monkeypatch):
    monkeypatch.setattr(analyze, "_ollama_up", lambda _m: False)
    cfg = SimpleNamespace(vision_provider="auto", ollama_vision_model="moondream", gemini_api_key="")
    assert analyze.describe("/tmp/x.jpg", "anything", cfg) is None


def test_auto_prefers_the_local_model(monkeypatch):
    cfg = SimpleNamespace(vision_provider="auto", ollama_vision_model="moondream",
                          gemini_api_key="key")
    assert analyze.available(cfg) == "ollama"
