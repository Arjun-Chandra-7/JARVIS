"""One plain completion: a system prompt, a user prompt, text back. No tools, no history.

For handlers that have already gathered everything the model needs and want only the wording —
describing a project, explaining a transcript excerpt. Going through the full agent for this
sends ninety-odd tool schemas and the whole system prompt alongside, which is slower and gives a
small model the chance to decide to do something else instead.
"""
from __future__ import annotations

import asyncio
import os

# Measured 2026-09-23 on the tutor prompt: 0.6 s, correct, grounded, answered in Hinglish.
FALLBACK_GROQ_MODEL = "qwen/qwen3.8-27b"


def candidates(settings, strength: str = "default") -> list[tuple[str, str, str]]:
    """(base_url, key, model) to try, in order.

    ``default`` is the configured brain. ``strong`` is for teaching and explanation, where a
    wrong step is worse than a slow one: measured on this machine, the local 3B model explained
    Pythagoras as "the sum of the sides" in 10.8 s. It prefers a configured cloud model and falls
    back to the configured brain, so it never needs more setup than the brain already has.
    """
    configured = settings.llm_params()
    if strength != "strong":
        return [configured]
    out: list[tuple[str, str, str]] = []
    groq = "https://api.groq.com/openai/v1"
    if settings.groq_api_key:
        # JARVIS_STRONG_MODEL first when set; then the configured Groq model; then one known to be
        # served today — the configured one had been retired (404) when this was written.
        for model in (os.environ.get("JARVIS_STRONG_MODEL", ""), settings.groq_model, FALLBACK_GROQ_MODEL):
            if model and (groq, settings.groq_api_key, model) not in out:
                out.append((groq, settings.groq_api_key, model))
    if settings.gemini_api_key:
        out.append(("https://generativelanguage.googleapis.com/v1beta/openai/",
                    settings.gemini_api_key, settings.gemini_model))
    if configured not in out:
        out.append(configured)
    return out


async def complete(system: str, prompt: str, config=None, temperature: float = 0.2,
                   timeout: float = 60.0, strength: str = "default") -> str:
    """The model's answer, or "" when there is no model to ask (never a traceback read aloud)."""
    from .config import CONFIG

    settings = config or CONFIG

    def ask() -> str:
        from openai import OpenAI

        for base_url, api_key, model in candidates(settings, strength):
            try:
                client = OpenAI(base_url=base_url, api_key=api_key or "none", max_retries=0, timeout=timeout)
                done = client.chat.completions.create(
                    model=model, temperature=temperature,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}])
                text = (done.choices[0].message.content or "").strip()
                if text:
                    return text
            except Exception:  # noqa: BLE001 — try the next one
                continue
        return ""

    return await asyncio.to_thread(ask)
