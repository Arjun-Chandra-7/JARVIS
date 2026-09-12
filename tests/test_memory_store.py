"""The hybrid SQLite memory: FTS5 keyword + vector cosine, fused with RRF."""

from __future__ import annotations

import time

import pytest

from jarvis.memory.store import MemoryStore, chunk_markdown


def fake_embed(text: str):
    """A deterministic toy embedding: one dimension per keyword, so cosine is predictable."""
    words = ("sister", "meera", "coffee", "python", "rain", "guitar")
    low = (text or "").lower()
    return [1.0 if w in low else 0.0 for w in words] or [0.0] * len(words)


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")


def test_chunk_markdown_packs_paragraphs():
    text = "\n\n".join(["a" * 500, "b" * 500, "c" * 500])
    chunks = chunk_markdown(text, max_chars=1200)
    assert len(chunks) == 2
    assert all(len(c) <= 1200 for c in chunks)


def test_chunk_markdown_hard_splits_a_giant_paragraph():
    chunks = chunk_markdown("x" * 3000, max_chars=1000)
    assert len(chunks) == 3
    assert all(len(c) <= 1000 for c in chunks)


def test_episode_roundtrip(store):
    store.add_episode("you", "I take my coffee black")
    hits = store.search("coffee", kinds=["episode"])
    assert hits and "coffee" in hits[0]["text"]
    assert hits[0]["kind"] == "episode"


def test_keyword_search_works_without_any_embedder(store):
    store.add_fact("Arjun's sister is called Meera")
    hits = store.search("who is Meera")
    assert hits and "Meera" in hits[0]["text"]
    assert hits[0]["matched"] == ["keyword"]


def test_semantic_search_finds_what_keywords_miss(store):
    store.add_fact("Arjun's sister is called Meera", embed=fake_embed)
    store.add_fact("The guitar is in the hall", embed=fake_embed)
    # "sibling" shares no token with the stored text, so only the vector half can fire.
    hits = store.search("sister", kinds=["fact"], embed=fake_embed)
    assert hits
    assert "Meera" in hits[0]["text"]
    assert "semantic" in hits[0]["matched"]


def test_agreement_between_signals_outranks_a_single_signal(store):
    store.add_fact("rain is forecast for tuesday", embed=fake_embed)   # keyword + vector
    store.add_fact("guitar strings need replacing", embed=fake_embed)  # neither
    store.add_episode("you", "the rain never stopped", embed=fake_embed)
    hits = store.search("rain", embed=fake_embed)
    assert hits[0]["matched"] == ["keyword", "semantic"]


def test_facts_are_deduplicated_not_accumulated(store):
    for _ in range(3):
        store.add_fact("Arjun drinks his coffee black")
    assert store.stats()["fact"] == 1


def test_forget_removes_a_source(store):
    store.add_fact("temporary", ref="scratch")
    assert store.forget("fact", "scratch") == 1
    assert store.search("temporary") == []


def test_deleting_a_chunk_also_clears_it_from_keyword_search(store):
    store.add_fact("distinctiveword", ref="scratch", embed=fake_embed)
    store.forget("fact", "scratch")
    assert store.search("distinctiveword") == []


def test_prune_episodes_only_drops_old_conversation(store):
    store.add_episode("you", "ancient history", ts=time.time() - 500 * 86400)
    store.add_episode("you", "said today")
    store.add_fact("a durable fact")
    assert store.prune_episodes(keep_days=400) == 1
    assert store.stats()["episode"] == 1
    assert store.stats()["fact"] == 1


def test_sync_vault_is_incremental(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Notes").mkdir(parents=True)
    (vault / "Notes" / "a.md").write_text("Meera is my sister.\n\nShe plays guitar.")
    store = MemoryStore(tmp_path / "m.db")

    first = store.sync_vault(vault)
    assert first["indexed"] == 1 and first["chunks"] == 1

    second = store.sync_vault(vault)
    assert second["indexed"] == 0 and second["unchanged"] == 1


def test_sync_vault_reindexes_a_changed_file_and_drops_a_deleted_one(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "a.md"
    note.write_text("original text")
    store = MemoryStore(tmp_path / "m.db")
    store.sync_vault(vault)

    note.write_text("replacement text")
    import os

    os.utime(note, (time.time() + 5, time.time() + 5))
    store.sync_vault(vault)
    assert store.search("original") == []
    assert store.search("replacement")

    note.unlink()
    assert store.sync_vault(vault)["removed"] == 1
    assert store.search("replacement") == []


def test_sync_vault_skips_private_material(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Jarvis" / "private").mkdir(parents=True)
    (vault / "Jarvis" / "private" / "chats.md").write_text("secret imported chat")
    (vault / "open.md").write_text("ordinary note")
    store = MemoryStore(tmp_path / "m.db")
    store.sync_vault(vault)
    assert store.search("secret") == []
    assert store.search("ordinary")


def test_kind_filter_is_respected(store):
    store.add_fact("coffee is a fact")
    store.add_episode("you", "coffee was said")
    assert {h["kind"] for h in store.search("coffee", kinds=["fact"])} == {"fact"}


def test_recent_episodes_are_chronological(store):
    now = time.time()
    store.add_episode("you", "first", ts=now - 20)
    store.add_episode("jarvis", "second", ts=now - 10)
    got = store.recent_episodes(limit=5)
    assert [g["text"] for g in got] == ["first", "second"]


def test_query_punctuation_cannot_break_the_fts_syntax(store):
    store.add_fact("normal content")
    assert store.search('"; DROP TABLE chunks; --') == []
    assert store.search("AND OR NOT *") == []
    assert store.stats()["fact"] == 1


def test_empty_query_returns_nothing(store):
    store.add_fact("something")
    assert store.search("   ") == []


def test_stats_counts_each_kind(store):
    store.add_fact("f", embed=fake_embed)
    store.add_episode("you", "e", embed=fake_embed)
    s = store.stats()
    assert s["fact"] == 1 and s["episode"] == 1 and s["vectors"] == 2
