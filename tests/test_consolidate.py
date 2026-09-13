"""Nightly consolidation: episodes in, durable facts out, contradictions superseded."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from jarvis.memory import consolidate
from jarvis.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "m.db")


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(vault_path=tmp_path / "vault", llm_params=lambda: ("", "", ""))


def seed(store, n=10, text="the flight to Delhi is on the 24th, seat 14C"):
    for i in range(n):
        store.add_episode("you" if i % 2 == 0 else "jarvis", f"{text} ({i})")


def llm_returning(facts):
    return lambda _prompt: json.dumps(facts)


def test_parse_facts_accepts_a_bare_array():
    got = consolidate._parse_facts('[{"fact": "Arjun takes coffee black", "subject": "coffee"}]')
    assert got == [{"fact": "Arjun takes coffee black", "subject": "coffee"}]


def test_parse_facts_survives_a_code_fence_and_prose():
    raw = 'Sure!\n```json\n[{"fact": "Arjun flies on the 24th", "subject": "travel"}]\n```\n'
    assert consolidate._parse_facts(raw)[0]["subject"] == "travel"


def test_parse_facts_rejects_garbage():
    assert consolidate._parse_facts("no json here") == []
    assert consolidate._parse_facts("[not json]") == []
    assert consolidate._parse_facts('[{"fact": "hi"}]') == []      # too short to be a fact


def test_parse_facts_defaults_a_missing_subject():
    assert consolidate._parse_facts('[{"fact": "Arjun prefers evenings"}]')[0]["subject"] == "general"


def test_too_few_episodes_is_a_no_op(store, config):
    seed(store, n=2)
    out = consolidate.consolidate(config, llm=llm_returning([]), store=store, embed=None)
    assert out["added"] == 0
    assert "not enough" in out["skipped"]


def test_facts_are_written_from_episodes(store, config):
    seed(store)
    out = consolidate.consolidate(
        config,
        llm=llm_returning([{"fact": "Arjun's flight to Delhi is on the 24th, seat 14C", "subject": "travel"}]),
        store=store, embed=None,
    )
    assert out["added"] == 1
    hits = store.search("seat", kinds=["fact"])
    assert hits and "14C" in hits[0]["text"]


def test_the_episode_window_is_respected(store, config):
    old = time.time() - 60 * 3600
    for i in range(20):
        store.add_episode("you", f"ancient {i}", ts=old)
    out = consolidate.consolidate(config, llm=llm_returning([]), store=store, embed=None)
    assert out["episodes"] == 0
    assert "not enough" in out["skipped"]


def test_a_contradicting_fact_supersedes_the_old_one(store, config):
    store.add_fact("Arjun's flight to Delhi is on the 24th at 6am", ref="travel")
    seed(store)
    out = consolidate.consolidate(
        config,
        llm=llm_returning([{"fact": "Arjun's flight to Delhi is on the 26th at 6am", "subject": "travel"}]),
        store=store, embed=None,
    )
    assert out["superseded"] == 1
    assert store.stats()["fact"] == 1
    assert "26th" in store.search("flight Delhi", kinds=["fact"])[0]["text"]


def test_an_unrelated_fact_does_not_supersede(store, config):
    store.add_fact("Arjun's flight to Delhi is on the 24th at 6am", ref="travel")
    seed(store)
    out = consolidate.consolidate(
        config,
        llm=llm_returning([{"fact": "Arjun's sister is called Meera and lives in Pune", "subject": "family"}]),
        store=store, embed=None,
    )
    assert out["added"] == 1 and out["superseded"] == 0
    assert store.stats()["fact"] == 2


def test_supersede_uses_embeddings_when_available(store, config):
    # Two sentences with almost no shared vocabulary that the embedder says are the same claim.
    def embed(text):
        return [1.0, 0.0] if "delhi" in text.lower() or "capital" in text.lower() else [0.0, 1.0]

    store.add_fact("Arjun travels to Delhi on the 24th", ref="travel", embed=embed)
    seed(store)
    out = consolidate.consolidate(
        config,
        llm=llm_returning([{"fact": "The capital trip moved to Thursday", "subject": "travel"}]),
        store=store, embed=embed,
    )
    assert out["superseded"] == 1


def test_a_failing_model_leaves_the_store_alone(store, config):
    seed(store)

    def boom(_p):
        raise RuntimeError("offline")

    before = store.stats()
    out = consolidate.consolidate(config, llm=boom, store=store, embed=None)
    assert "model error" in out["skipped"]
    assert store.stats() == before


def test_empty_extraction_still_marks_the_run(store, config):
    seed(store)
    assert consolidate.last_run() == 0.0
    out = consolidate.consolidate(config, llm=llm_returning([]), store=store, embed=None)
    assert out["skipped"] == "nothing durable found"
    assert consolidate.last_run() > 0


def test_due_respects_the_interval(store, config):
    now = time.time()
    assert consolidate.due(now=now) is True
    seed(store)
    consolidate.consolidate(config, llm=llm_returning([]), store=store, embed=None, now=now)
    assert consolidate.due(now=now) is False
    assert consolidate.due(now=now + 21 * 3600) is True


def test_no_model_available_is_reported_not_raised(store, config):
    seed(store)
    out = consolidate.consolidate(config, llm=None, store=store, embed=None)
    assert out["skipped"] == "no model available"
