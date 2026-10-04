"""Jarvis's local Ollama models run on the processor, so Jarvis stays inside its share of the GPU.

Jarvis is allowed 0.75 GB of the 4 GB card (set on 4 Oct). Speech recognition earns its place
there — whisper small takes a fifth of a second on the GPU and four on the processor — and fits in
about 390 MB. The local LLM does not: the startup health check sent qwen2.5:3b one "ping", Ollama
put all 2.1 GB of it on the card, and OLLAMA_KEEP_ALIVE=-1 left it there for good — for a model
that only answers when every cloud model has failed. Embeddings and the moondream fallback would
have done the same the first time they were asked.

Ollama has no per-client switch for this: its /v1 endpoint ignores ``options``, and a /v1 request
reloads a processor-only model back onto the GPU. So each local model gets a twin, created from it
with one parameter added — ``num_gpu 0`` — and that twin's name is what goes on the wire. Same
weights, same template, same answers; nothing is downloaded or copied, and the configured names
(which key cached vectors and tool-calling verdicts) do not change. The twin is re-created once
per process, which takes a tenth of a second and follows a re-pulled parent.

JARVIS_OLLAMA_GPU=1 turns this off.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

SUFFIX = "-jarvis-cpu"
DEFAULT_ROOT = "http://localhost:11434"
RETRY_S = 60.0                      # Ollama down or the model not pulled: ask again after this

_LOCK = threading.Lock()
_MADE: dict[tuple[str, str], tuple[float, str]] = {}


def enabled() -> bool:
    return os.environ.get("JARVIS_OLLAMA_GPU", "").strip().lower() not in {"1", "true", "yes"}


def twin_name(model: str) -> str:
    """The processor-only twin's name: ``qwen2.5:3b`` -> ``qwen2.5:3b-jarvis-cpu``."""
    if model.endswith(SUFFIX):
        return model
    tail = model.rsplit("/", 1)[-1]
    return f"{model}{SUFFIX}" if ":" in tail else f"{model}:latest{SUFFIX}"


def logical(name: str) -> str:
    """The model a twin stands for, named the way Ollama lists it (``moondream:latest``)."""
    return name[: -len(SUFFIX)] if name.endswith(SUFFIX) else name


def is_twin(name: str) -> bool:
    return name.endswith(SUFFIX)


def root_of(base_url: str) -> str:
    """``http://localhost:11434/v1`` -> ``http://localhost:11434``."""
    return (base_url or DEFAULT_ROOT).rstrip("/").rsplit("/v1", 1)[0]


def _default_post(url: str, body: dict) -> int:
    import httpx

    return httpx.post(url, json=body, timeout=30).status_code


def wire(model: str, root: str = DEFAULT_ROOT, post: Optional[Callable[[str, dict], int]] = None) -> str:
    """The name to send Ollama for `model`: its processor-only twin, or `model` if that fails.

    `post(url, body) -> status` lets a caller route this through its own client (tests, adapters).
    """
    if not model or not enabled() or is_twin(model):
        return model
    root = root_of(root)
    key = (root, model)
    with _LOCK:
        hit = _MADE.get(key)
        if hit and (hit[1] != model or time.monotonic() - hit[0] < RETRY_S):
            return hit[1]
    twin = twin_name(model)
    body = {"model": twin, "from": model, "parameters": {"num_gpu": 0}, "stream": False}
    try:
        ok = (post or _default_post)(root + "/api/create", body) < 400
    except Exception:  # noqa: BLE001 - Ollama down: use the plain name, try again later
        ok = False
    name = twin if ok else model
    with _LOCK:
        _MADE[key] = (time.monotonic(), name)
    return name


def forget() -> None:
    with _LOCK:
        _MADE.clear()
