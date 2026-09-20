"""One name for the embedding model, and the corruption that happens without it.

The failure this prevents is silent. Two models do not share a vector space, so a cosine between
their vectors is a number with no meaning — an index holding both does not raise, it just returns
worse answers, and nothing in a test suite notices. That is why the mismatch is checked rather
than assumed.
"""

from __future__ import annotations

import pytest

from jarvis.agent import tool_router
from jarvis.memory import embedding_model, embeddings, index


def test_the_default_is_what_the_vault_was_built_with(monkeypatch):
    """Changing the default silently would invalidate an index nobody was told to rebuild."""
    monkeypatch.delenv(embedding_model.ENV_VAR, raising=False)
    assert embedding_model.name() == "nomic-embed-text"


def test_the_environment_chooses(monkeypatch):
    monkeypatch.setenv(embedding_model.ENV_VAR, "qwen3-embedding:0.6b")
    assert embedding_model.name() == "qwen3-embedding:0.6b"


def test_blank_or_whitespace_falls_back_rather_than_asking_ollama_for_nothing(monkeypatch):
    monkeypatch.setenv(embedding_model.ENV_VAR, "   ")
    assert embedding_model.name() == embedding_model.DEFAULT


def test_an_index_built_with_another_model_is_reported_stale(monkeypatch):
    monkeypatch.setenv(embedding_model.ENV_VAR, "embeddinggemma")
    assert embedding_model.stale_vault_index("nomic-embed-text") is True
    assert embedding_model.stale_vault_index("embeddinggemma") is False


def test_an_index_with_no_recorded_model_is_not_called_stale(monkeypatch):
    """Every index written before this existed has no model recorded. Declaring those stale
    would rebuild the vault on first run for no reason."""
    monkeypatch.delenv(embedding_model.ENV_VAR, raising=False)
    assert embedding_model.stale_vault_index(None) is False
    assert embedding_model.stale_vault_index("") is False


# --------------------------------------------------------------- everyone resolves the same way
def test_every_caller_lands_on_the_same_model(monkeypatch):
    """The point of the module. Five defaults across two subsystems is how four get changed and
    one does not."""
    monkeypatch.setenv(embedding_model.ENV_VAR, "embeddinggemma")

    asked: list[str] = []
    monkeypatch.setattr(embeddings.httpx, "post",
                        lambda *a, **k: asked.append(k["json"]["model"]) or _Fail())
    embeddings.embed("anything")                       # no model passed
    assert asked == ["embeddinggemma"]

    assert tool_router._model_name("") == "embeddinggemma"
    assert index.VaultIndex.__init__.__defaults__ == ("",), \
        "the vault index went back to a hard-coded default"


def test_an_explicit_model_still_wins(monkeypatch):
    """Resolution is a fallback, not an override — a caller that names one means it."""
    monkeypatch.setenv(embedding_model.ENV_VAR, "embeddinggemma")
    assert tool_router._model_name("nomic-embed-text") == "nomic-embed-text"


class _Fail:
    """A response object that makes embed() give up quietly."""

    def raise_for_status(self):
        raise RuntimeError("no ollama in a test")


@pytest.mark.parametrize("recorded", ["nomic-embed-text", "embeddinggemma", "qwen3-embedding:0.6b"])
def test_a_model_is_never_stale_against_itself(recorded, monkeypatch):
    monkeypatch.setenv(embedding_model.ENV_VAR, recorded)
    assert embedding_model.stale_vault_index(recorded) is False
