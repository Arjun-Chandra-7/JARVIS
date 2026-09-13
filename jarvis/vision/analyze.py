"""Image understanding for a text-only brain (Groq): route a picture through a vision model and
return a text description the brain can act on.

Two backends, auto-selected:
  - ollama : fully local (e.g. `moondream`), $0 and unlimited — great with a GPU.
  - gemini : Google's free API (needs GEMINI_API_KEY), no local resources.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

_DEFAULT_Q = "Describe what is on this screen in detail, including any text, windows, and errors."


def _b64(path: str) -> Optional[str]:
    try:
        return base64.b64encode(Path(path).read_bytes()).decode()
    except Exception:  # noqa: BLE001
        return None


def _ollama_up(model: str) -> bool:
    import httpx

    try:
        tags = httpx.get("http://localhost:11434/api/tags", timeout=2).json()
        names = [m.get("name", "") for m in tags.get("models", [])]
        return any(model.split(":")[0] in n for n in names)
    except Exception:  # noqa: BLE001
        return False


def _ollama(path: str, question: str, model: str) -> Optional[str]:
    import httpx

    img = _b64(path)
    if not img:
        return None
    payload = {"model": model, "prompt": question, "images": [img], "stream": False}
    try:
        for _ in range(2):  # first call may return empty while the model cold-loads
            r = httpx.post("http://localhost:11434/api/generate", json=payload, timeout=120)
            out = (r.json().get("response") or "").strip()
            if out:
                return out
        return None
    except Exception as exc:  # noqa: BLE001
        return f"(vision error: {exc})"


def _gemini(path: str, question: str, key: str) -> Optional[str]:
    import httpx

    img = _b64(path)
    if not img:
        return None
    try:
        r = httpx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            params={"key": key},
            json={"contents": [{"parts": [
                {"text": question},
                {"inline_data": {"mime_type": "image/jpeg", "data": img}},
            ]}]},
            timeout=30,
        )
        j = r.json()
        return j["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as exc:  # noqa: BLE001
        return f"(vision error: {exc})"


def available(config) -> str | None:
    prov = getattr(config, "vision_provider", "auto")
    if prov == "gemini":
        return "gemini" if config.gemini_api_key else None
    if prov == "ollama":
        return "ollama" if _ollama_up(config.ollama_vision_model) else None
    # auto: local first (free, no quota), then Gemini
    if _ollama_up(config.ollama_vision_model):
        return "ollama"
    return "gemini" if config.gemini_api_key else None


#  A short, pointed question is exactly what moondream is worst at. Asked "What application is
#  open?" against a screenshot of VS Code it replied "xtr"; asked to describe the same image it
#  produced an accurate paragraph naming the code editor. It is a captioner, not a VQA model at
#  this size, so the division of labour is: the vision model describes, the language model answers.
_MIN_USEFUL = 40


def _looks_degenerate(text: Optional[str]) -> bool:
    if not text:
        return True
    t = text.strip()
    return len(t) < _MIN_USEFUL or t.startswith("(vision error")


def describe(path: str, question: str, config) -> Optional[str]:
    """Return a text description/answer about the image, or None if no vision backend is set up.

    Gemini handles a direct question well and gets it verbatim. The local model is asked to
    describe the image first; only if that succeeds is the user's question appended, and a
    degenerate answer falls back to the description, which the brain can still reason over.
    """
    prov = available(config)
    if prov == "gemini":
        return _gemini(path, question or _DEFAULT_Q, config.gemini_api_key)
    if prov != "ollama":
        return None

    model = config.ollama_vision_model
    if not question:
        return _ollama(path, _DEFAULT_Q, model)

    # Ask the pointed question, but only trust a substantive answer.
    focused = _ollama(path, f"{_DEFAULT_Q}\n\nPay particular attention to: {question}", model)
    if not _looks_degenerate(focused):
        return focused
    described = _ollama(path, _DEFAULT_Q, model)
    if _looks_degenerate(described):
        return focused or described
    return (
        f"{described}\n\n(The screen description above is from a small local vision model; "
        f"answer the user's question — \"{question}\" — from it.)"
    )
