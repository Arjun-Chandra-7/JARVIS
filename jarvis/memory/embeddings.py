"""Local text embeddings via Ollama (nomic-embed-text). Fully local & free; optional.

If Ollama isn't running, `available()` returns False and callers fall back to keyword search.
Install: https://ollama.com  then  `ollama pull nomic-embed-text`.
"""

from __future__ import annotations

from typing import Optional

import httpx

_BASE = "http://localhost:11434"


def available(timeout: float = 1.0) -> bool:
    try:
        httpx.get(f"{_BASE}/api/tags", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def embed(text: str, model: str = "", timeout: float = 30.0) -> Optional[list[float]]:
    # Resolved here rather than in the signature so every caller gets the same model, including
    # the ones that pass nothing. Two models do not share a vector space, and an index that
    # mixes them does not error — it just answers worse.
    from . import embedding_model

    model = model or embedding_model.name()
    try:
        resp = httpx.post(
            f"{_BASE}/api/embeddings", json={"model": model, "prompt": text}, timeout=timeout
        )
        resp.raise_for_status()
        return resp.json().get("embedding")
    except Exception:  # noqa: BLE001
        return None
