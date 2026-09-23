"""One plain completion: a system prompt, a user prompt, text back. No tools, no history.

For handlers that have already gathered everything the model needs and want only the wording —
describing a project, explaining a transcript excerpt. Going through the full agent for this
sends ninety-odd tool schemas and the whole system prompt alongside, which is slower and gives a
small model the chance to decide to do something else instead.

Two strengths. ``default`` is the configured brain. ``strong`` is for teaching and understanding,
where a wrong step is worse than none: it tries the strong providers in order, skipping any whose
circuit breaker is open (providers.py), and does **not** fall back to the weak local model unless
``JARVIS_ALLOW_WEAK_TEACHING=1``. When it cannot answer it says why, so the caller can tell the
person instead of reading out a confident mistake.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from . import providers as pv


@dataclass
class Completion:
    text: str = ""
    provider: str = ""                   # "groq:qwen/…" that answered
    quality: str = ""                    # "strong" / "weak"
    failures: list[str] = field(default_factory=list)   # "gemini:…: permission_denied"
    weak_only: bool = False              # a weak model was available but not used

    @property
    def ok(self) -> bool:
        return bool(self.text)

    def unavailable_message(self) -> str:
        if self.weak_only:
            return ("Only the local model is available right now, and it isn't reliable enough for "
                    "explanations — it gets steps wrong. " + self._why())
        return "No model is reachable right now. " + self._why()

    def _why(self) -> str:
        return f"({'; '.join(self.failures)})" if self.failures else ""


def candidates(settings, strength: str = "default") -> list[pv.Provider]:
    """The providers to try, in order."""
    if strength != "strong":
        return [pv.for_brain(settings)]
    return pv.configured(settings)


def _ask(provider: pv.Provider, system: str, prompt: str, temperature: float, timeout: float) -> str:
    from openai import OpenAI

    client = OpenAI(base_url=provider.base_url, api_key=provider.key or "none", max_retries=0, timeout=timeout)
    done = client.chat.completions.create(
        model=provider.model, temperature=temperature,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}])
    return (done.choices[0].message.content or "").strip()


def complete_sync(system: str, prompt: str, settings, temperature: float = 0.2, timeout: float = 60.0,
                  strength: str = "default", ask=None) -> Completion:
    ask = ask or _ask                    # looked up per call, so tests can stand in for the network
    allow_weak = os.environ.get("JARVIS_ALLOW_WEAK_TEACHING", "") in {"1", "true", "yes"}
    out = Completion()
    for provider in candidates(settings, strength):
        if strength == "strong" and provider.quality == "weak" and not allow_weak:
            out.weak_only = True
            continue
        held = pv.blocked(provider)
        if held:
            out.failures.append(f"{provider.id}: {held['kind']} (paused)")
            continue
        try:
            text = ask(provider, system, prompt, temperature, timeout)
        except Exception as exc:  # noqa: BLE001 — classified, remembered, and the next one tried
            failure = pv.classify(exc)
            pv.record_failure(provider, failure)
            out.failures.append(f"{provider.id}: {failure.kind}")
            continue
        pv.record_success(provider)
        if text:
            out.text, out.provider, out.quality = text, provider.id, provider.quality
            out.weak_only = False
            return out
    return out


async def complete_detailed(system: str, prompt: str, config=None, temperature: float = 0.2,
                            timeout: float = 60.0, strength: str = "default") -> Completion:
    from .config import CONFIG
    return await asyncio.to_thread(complete_sync, system, prompt, config or CONFIG, temperature,
                                   timeout, strength)


async def complete(system: str, prompt: str, config=None, temperature: float = 0.2,
                   timeout: float = 60.0, strength: str = "default") -> str:
    """The model's answer, or "" when there is no model to ask (never a traceback read aloud)."""
    return (await complete_detailed(system, prompt, config, temperature, timeout, strength)).text
