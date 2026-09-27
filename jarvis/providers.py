"""Model providers: which ones work, which ones don't, and what to do about each.

Found on 2026-09-23: the configured Groq model had been retired (404), Gemini answered every
request with 403 ("project has been denied access"), and every explanation had silently fallen
through to the local 3B model — which explained Pythagoras as "the sum of the sides". Nothing
said so. This module is where that stops:

* ``classify`` turns a provider error into a kind with a remedy a person can act on.
* A circuit breaker, shared across processes through a small state file, stops a provider that
  is failing *persistently* from being asked again on every request: a 403 or a retired model
  stays open for hours, a rate limit for as long as the provider asked, an outage briefly.
* ``health_check`` asks each configured provider for its model list and checks the configured
  model is on it, then sends a one-token probe: Gemini *lists* its models happily and still
  answers every generation with 403, so a list alone reported it healthy. Startup and ``--check``.
* Every provider has a quality tier. Teaching and screen understanding ask for ``strong`` and
  are told plainly when only the weak local model is left, instead of getting its answer.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# The one place model defaults live. Checked against the providers' own model lists on the date
# above; the startup health check says so when one of them goes away.
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"
DEFAULT_GROQ_FALLBACK = "openai/gpt-oss-20b"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"
OPEN_STRONG = tuple(m.strip() for m in os.environ.get(
    "JARVIS_OPEN_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b").split(",") if m.strip())

GROQ_URL = "https://api.groq.com/openai/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

MODEL_GONE, PERMISSION, AUTH, RATE_LIMIT, OUTAGE, OTHER = (
    "model_not_found", "permission_denied", "auth_failed", "rate_limited", "provider_outage", "other")

# How long a failure keeps a provider out of rotation. A retired model or a denied project will not
# fix itself in minutes; a rate limit or a 502 usually does.
COOLDOWN_S = {MODEL_GONE: 6 * 3600, PERMISSION: 6 * 3600, AUTH: 6 * 3600,
              RATE_LIMIT: 60, OUTAGE: 30, OTHER: 30}
MAX_OUTAGE_COOLDOWN_S = 600

REMEDY = {
    MODEL_GONE: "the model name is retired or wrong — set a current one in .env",
    PERMISSION: "the account or project was denied access — check it in the provider's console",
    AUTH: "the API key was rejected — replace it in .env",
    RATE_LIMIT: "rate limited — it will be tried again shortly",
    OUTAGE: "the provider is down or unreachable — it will be retried",
    OTHER: "unexpected error",
}


@dataclass
class Provider:
    name: str            # "groq", "gemini", "ollama"
    base_url: str
    key: str
    model: str
    quality: str         # "strong" or "weak"

    @property
    def id(self) -> str:
        return f"{self.name}:{self.model}"


@dataclass
class Failure:
    kind: str
    detail: str
    retry_after: Optional[float] = None


def classify(exc: BaseException) -> Failure:
    """What kind of failure an OpenAI-compatible client raised."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc)
    low = text.lower()
    detail = re.sub(r"(?i)(key|token|bearer)[=: ]+\S+", r"\1=<redacted>", text)[:200]
    retry = None
    response = getattr(exc, "response", None)
    try:
        header = response.headers.get("retry-after") if response is not None else None
        retry = float(header) if header else None
    except (AttributeError, TypeError, ValueError):
        retry = None
    if status == 404 or "does not exist" in low or "decommissioned" in low or "model_not_found" in low \
            or ("model" in low and "not found" in low):
        return Failure(MODEL_GONE, detail)
    if status == 401 or "invalid api key" in low or "invalid_api_key" in low or "unauthorized" in low:
        return Failure(AUTH, detail)
    if status == 403 or "permission" in low or "denied access" in low or "forbidden" in low:
        return Failure(PERMISSION, detail)
    if status == 429 or "rate limit" in low or "rate_limit" in low or "quota" in low:
        return Failure(RATE_LIMIT, detail, retry)
    name = type(exc).__name__.lower()
    if (isinstance(status, int) and status >= 500) or "timeout" in name or "connection" in name \
            or "timed out" in low or "connection" in low or "unavailable" in low:
        return Failure(OUTAGE, detail)
    return Failure(OTHER, detail)


# --------------------------------------------------------------------------- the breaker

def _state_path() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser() / "provider-health.json"


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass


def record_failure(provider: Provider, failure: Failure, now: Optional[float] = None) -> float:
    """Open the breaker for this provider+model. Returns the cooldown in seconds."""
    now = time.time() if now is None else now
    data = _load()
    entry = data.get(provider.id, {})
    cooldown = COOLDOWN_S[failure.kind]
    if failure.kind == RATE_LIMIT and failure.retry_after:
        cooldown = max(1.0, failure.retry_after)
    if failure.kind == OUTAGE:
        streak = int(entry.get("streak", 0)) + 1 if entry.get("kind") == OUTAGE else 1
        cooldown = min(MAX_OUTAGE_COOLDOWN_S, COOLDOWN_S[OUTAGE] * 2 ** (streak - 1))
        entry["streak"] = streak
    entry.update(kind=failure.kind, detail=failure.detail, until=now + cooldown, at=now)
    data[provider.id] = entry
    _save(data)
    return cooldown


def record_success(provider: Provider) -> None:
    data = _load()
    if provider.id in data:
        data.pop(provider.id)
        _save(data)


def blocked(provider: Provider, now: Optional[float] = None) -> Optional[dict]:
    """The open breaker for this provider, or None when it may be asked."""
    now = time.time() if now is None else now
    entry = _load().get(provider.id)
    if entry and float(entry.get("until", 0)) > now:
        return entry
    return None


def reset(provider: Optional[Provider] = None) -> None:
    data = _load()
    if provider is None:
        data = {}
    else:
        data.pop(provider.id, None)
    _save(data)


# --------------------------------------------------------------------------- the providers we have

def configured(config) -> list[Provider]:
    """Every provider with credentials, strongest first. The local model is always last."""
    out = []
    strong_override = os.environ.get("JARVIS_STRONG_MODEL", "").strip()
    if config.groq_api_key:
        # Open-weight models on Groq, each with its own per-minute quota, so one running dry is not
        # "no model is reachable". GPT-OSS first: measured 24 Sep, qwen3.8-27b answered 429 "request
        # too large" to a one-word prompt on this plan while gpt-oss-120b answered in 1.1 s.
        # Then the configured model and the known-current default, so a retired name in .env costs
        # one classified 404 (and a paused breaker) rather than every answer.
        for model in dict.fromkeys(m for m in (strong_override, *OPEN_STRONG, config.groq_model, DEFAULT_GROQ_MODEL) if m):
            out.append(Provider("groq", GROQ_URL, config.groq_api_key, model, "strong"))
    if config.gemini_api_key:
        out.append(Provider("gemini", GEMINI_URL, config.gemini_api_key, config.gemini_model, "strong"))
    out.append(Provider("ollama", config.ollama_base, "ollama", config.ollama_model,
                        os.environ.get("JARVIS_OLLAMA_QUALITY", "weak")))
    return out


def for_brain(config) -> Provider:
    """The provider the conversational brain uses, as a Provider."""
    base, key, model = config.llm_params()
    name = "gemini" if "googleapis" in base else "groq" if "groq.com" in base else "ollama"
    quality = "weak" if name == "ollama" and os.environ.get("JARVIS_OLLAMA_QUALITY", "weak") == "weak" else "strong"
    return Provider(name, base, key, model, quality)


# --------------------------------------------------------------------------- health

@dataclass
class Health:
    provider: Provider
    ok: bool
    kind: str = ""
    detail: str = ""
    available_models: list[str] = field(default_factory=list)

    def line(self) -> str:
        p = self.provider
        if self.ok:
            return f"{p.name} · {p.model}: ok ({p.quality})"
        suggestion = ""
        if self.kind == MODEL_GONE and self.available_models:
            usable = [m for m in self.available_models if not re.search(r"whisper|tts|guard|orpheus|playai", m)]
            suggestion = f" Available: {', '.join(usable[:6])}."
        return f"{p.name} · {p.model}: {REMEDY.get(self.kind, self.kind)}.{suggestion}"


def check(provider: Provider, timeout: float = 8.0, client=None) -> Health:
    """The configured model is on the provider's list, and it will generate one token."""
    try:
        if client is None:
            from openai import OpenAI
            client = OpenAI(base_url=provider.base_url, api_key=provider.key or "none", timeout=timeout,
                            max_retries=0)
        models = sorted(m.id for m in client.models.list().data)
    except Exception as exc:  # noqa: BLE001
        failure = classify(exc)
        record_failure(provider, failure)
        return Health(provider, False, failure.kind, failure.detail)
    wanted = provider.model
    present = any(m == wanted or m.endswith("/" + wanted) or m == "models/" + wanted for m in models)
    if not present:
        failure = Failure(MODEL_GONE, f"{wanted} is not in the provider's model list")
        record_failure(provider, failure)
        return Health(provider, False, MODEL_GONE, failure.detail, models)
    try:
        client.chat.completions.create(model=wanted, max_tokens=1,
                                       messages=[{"role": "user", "content": "ping"}])
    except Exception as exc:  # noqa: BLE001
        failure = classify(exc)
        record_failure(provider, failure)
        return Health(provider, False, failure.kind, failure.detail, models)
    record_success(provider)
    return Health(provider, True, available_models=models)


def health_check(config, timeout: float = 8.0) -> list[Health]:
    return [check(p, timeout) for p in configured(config)]


def summary(results: list[Health]) -> str:
    strong_ok = any(h.ok and h.provider.quality == "strong" for h in results)
    lines = [h.line() for h in results]
    if not strong_ok:
        lines.append("No strong model is available: study explanations and screen understanding are "
                     "off until one is; everyday commands still work.")
    return "\n".join(lines)
