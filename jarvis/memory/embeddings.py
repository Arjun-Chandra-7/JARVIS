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


def embed(text: str, model: str = "nomic-embed-text", timeout: float = 30.0) -> Optional[list[float]]:
    try:
        resp = httpx.post(
            f"{_BASE}/api/embeddings", json={"model": model, "prompt": text}, timeout=timeout
        )
        resp.raise_for_status()
        return resp.json().get("embedding")
    except Exception:  # noqa: BLE001
        return None
