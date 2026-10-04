"""The wire: talking to a provider, and saying precisely how it failed.

Adapter contract (every adapter implements these; see docs/DAILY_BRAIN.md → Provider adapters):

    chat(model, messages, key, *, tools=None, max_tokens, temperature, timeout,
         json_mode=False, on_delta=None) -> ChatResult
    list_models(key, timeout) -> list[str]

and raises ``ProviderError(kind, detail, retry_after, status)`` for every failure, where ``kind`` is
one of ``KINDS``. Nothing else leaves an adapter: no SDK exception, no response body with a key in
it, no retry. Retrying is the executor's decision, because only it knows the budget.

One adapter covers every provider this repository uses — Groq, Gemini (its OpenAI-compatible
endpoint), Ollama (``/v1``), and any OpenAI-compatible server the owner adds (OpenAI, OpenRouter,
llama.cpp). ``OllamaAdapter`` adds the local-model management calls.

httpx is used directly rather than the openai SDK so a test can hand in an ``httpx.MockTransport``
— a fake provider server — and no test can reach a real API by accident.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

AUTH, PERMISSION, MODEL_GONE = "auth_failed", "permission_denied", "model_not_found"
RATE_LIMIT, QUOTA, OUTAGE = "rate_limited", "quota_exhausted", "provider_outage"
TIMEOUT, NETWORK, MALFORMED, BAD_REQUEST, OTHER = "timeout", "network", "malformed", "bad_request", "other"
KINDS = (AUTH, PERMISSION, MODEL_GONE, RATE_LIMIT, QUOTA, OUTAGE, TIMEOUT, NETWORK, MALFORMED, BAD_REQUEST, OTHER)

# Failures that say something about the *key* rather than the model or the network.
KEY_FAULTS = {AUTH, PERMISSION, RATE_LIMIT, QUOTA}

_REDACT = re.compile(r"(?i)((?:api[_-]?key|key|token|bearer|authorization)[\"'=: ]+)[A-Za-z0-9_\-\.]{6,}")
_KEYISH = re.compile(r"\b(?:sk|gsk|sk-or|AIza)[-_A-Za-z0-9]{12,}\b")


def safe_detail(text: str) -> str:
    """An error message that can be shown and logged: no key, no long ids, short."""
    text = _REDACT.sub(r"\1<redacted>", str(text or ""))
    text = _KEYISH.sub("<redacted>", text)
    return " ".join(text.split())[:200]


class ProviderError(Exception):
    def __init__(self, kind: str, detail: str = "", retry_after: Optional[float] = None,
                 status: Optional[int] = None):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.retry_after, self.status = kind, safe_detail(detail), retry_after, status


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list = field(default_factory=list)     # [{"id", "name", "arguments": str}]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish: str = ""
    first_token_ms: Optional[float] = None
    total_ms: float = 0.0
    native_tool_calls: bool = False                    # came in the tool_calls field, not as text


def _retry_after(resp: httpx.Response, body_text: str) -> Optional[float]:
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    # Groq and Gemini also say it in words: "Please try again in 7.5s" / "retryDelay": "30s".
    m = re.search(r"(?i)(?:try again in|retry(?:Delay)?[\"':\s]+)\s*\"?(\d+(?:\.\d+)?)\s*(ms|s)", body_text)
    if m:
        val = float(m.group(1))
        return val / 1000 if m.group(2).lower() == "ms" else val
    return None


def classify_response(resp: httpx.Response) -> ProviderError:
    body = resp.text or ""
    low = body.lower()
    status = resp.status_code
    retry = _retry_after(resp, body)
    detail = body
    try:
        err = resp.json().get("error", {})
        if isinstance(err, list) and err:
            err = err[0].get("error", err[0])
        if isinstance(err, dict):
            detail = " ".join(str(err.get(k, "")) for k in ("code", "status", "type", "message")).strip() or body
        elif isinstance(err, str):
            detail = err
    except (ValueError, AttributeError):
        pass
    quota_words = ("insufficient_quota", "exceeded your current quota", "quota exceeded", "per day", "daily limit",
                   "billing", "credit balance", "out of credits", "requests per day", "tokens per day")
    if status == 404 or "model_not_found" in low or "does not exist" in low or "decommissioned" in low \
            or ("model" in low and "not found" in low) or "no longer supported" in low:
        return ProviderError(MODEL_GONE, detail, None, status)
    if status == 401 or "invalid api key" in low or "invalid_api_key" in low or "api key not valid" in low:
        return ProviderError(AUTH, detail, None, status)
    if status == 402 or any(w in low for w in quota_words) and status in (403, 429, 402):
        return ProviderError(QUOTA, detail, retry, status)
    if status == 403:
        return ProviderError(PERMISSION, detail, None, status)
    if status == 429:
        return ProviderError(RATE_LIMIT, detail, retry, status)
    if status in (408, 504):
        return ProviderError(TIMEOUT, detail, None, status)
    if status >= 500:
        return ProviderError(OUTAGE, detail, retry, status)
    if status == 400:
        return ProviderError(BAD_REQUEST, detail, None, status)
    return ProviderError(OTHER, detail, None, status)


def classify_exception(exc: BaseException) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(TIMEOUT, type(exc).__name__)
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return ProviderError(NETWORK, type(exc).__name__)
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):
        return ProviderError(MALFORMED, f"{type(exc).__name__}: {exc}")
    return ProviderError(OTHER, f"{type(exc).__name__}: {exc}")


class OpenAICompatibleAdapter:
    kind = "openai_compatible"

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None, extra_headers: dict | None = None):
        self.base_url = (base_url or "").rstrip("/")
        self.transport = transport
        self.extra_headers = extra_headers or {}

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)))

    def _headers(self, key: str) -> dict:
        h = {"Content-Type": "application/json", **self.extra_headers}
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def list_models(self, key: str = "", timeout: float = 8.0) -> list[str]:
        try:
            with self._client(timeout) as client:
                resp = client.get(f"{self.base_url}/models", headers=self._headers(key))
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        if resp.status_code >= 400:
            raise classify_response(resp)
        try:
            data = resp.json().get("data", resp.json().get("models", []))
            return sorted({str(m.get("id") or m.get("name", "")).removeprefix("models/") for m in data} - {""})
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(MALFORMED, f"model list: {type(exc).__name__}") from None

    def chat(self, model: str, messages: list, key: str = "", *, tools: list | None = None,
             max_tokens: int = 600, temperature: float = 0.3, timeout: float = 30.0, json_mode: bool = False,
             on_delta: Optional[Callable[[str], None]] = None) -> ChatResult:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        if on_delta is not None:
            body["stream"] = True
            return self._stream(body, key, timeout, on_delta, started)
        try:
            with self._client(timeout) as client:
                resp = client.post(f"{self.base_url}/chat/completions", headers=self._headers(key), json=body)
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        if resp.status_code >= 400:
            raise classify_response(resp)
        try:
            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]
            calls = [{"id": c.get("id", f"call_{i}"), "name": c["function"]["name"],
                      "arguments": c["function"].get("arguments") or "{}"}
                     for i, c in enumerate(msg.get("tool_calls") or [])]
            usage = data.get("usage") or {}
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(MALFORMED, f"response shape: {type(exc).__name__}") from None
        text = msg.get("content") or ""
        if not isinstance(text, str):
            raise ProviderError(MALFORMED, "content is not text")
        total = (time.monotonic() - started) * 1000
        return ChatResult(text.strip(), calls, int(usage.get("prompt_tokens") or 0),
                          int(usage.get("completion_tokens") or 0), choice.get("finish_reason") or "",
                          None, total, bool(calls))

    def _stream(self, body: dict, key: str, timeout: float, on_delta, started: float) -> ChatResult:
        text, calls, first, finish, usage = [], {}, None, "", {}
        try:
            with self._client(timeout) as client:
                with client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(key),
                                   json=body) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise classify_response(resp)
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        chunk = json.loads(payload)
                        usage = chunk.get("usage") or usage
                        if not chunk.get("choices"):
                            continue
                        ch = chunk["choices"][0]
                        finish = ch.get("finish_reason") or finish
                        delta = ch.get("delta") or {}
                        for c in delta.get("tool_calls") or []:
                            slot = calls.setdefault(c.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            slot["id"] = c.get("id") or slot["id"]
                            fn = c.get("function") or {}
                            slot["name"] = fn.get("name") or slot["name"]
                            slot["arguments"] += fn.get("arguments") or ""
                        piece = delta.get("content")
                        if piece:
                            if first is None:
                                first = (time.monotonic() - started) * 1000
                            text.append(piece)
                            if not calls:
                                on_delta(piece)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        out = [{"id": v["id"] or f"call_{i}", "name": v["name"], "arguments": v["arguments"] or "{}"}
               for i, v in sorted(calls.items())]
        return ChatResult("".join(text).strip(), out, int(usage.get("prompt_tokens") or 0),
                          int(usage.get("completion_tokens") or 0), finish, first,
                          (time.monotonic() - started) * 1000, bool(out))


class OllamaAdapter(OpenAICompatibleAdapter):
    """Ollama's /v1 for chat, plus its own API for what is installed and what is loaded."""

    kind = "ollama"

    @property
    def root(self) -> str:
        return self.base_url.rsplit("/v1", 1)[0]

    def _get(self, path: str, timeout: float = 4.0) -> dict:
        try:
            with self._client(timeout) as client:
                resp = client.get(self.root + path)
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        if resp.status_code >= 400:
            raise classify_response(resp)
        return resp.json()

    def _wire(self, model: str) -> str:
        """The processor-only twin of `model`, created through this adapter's own client."""
        from .. import ollama_cpu

        def post(url: str, body: dict) -> int:
            with self._client(30.0) as client:
                return client.post(url, json=body).status_code

        return ollama_cpu.wire(model, self.root, post)

    def chat(self, model: str, messages: list, key: str = "", **kwargs) -> ChatResult:
        return super().chat(self._wire(model), messages, key, **kwargs)

    def _tags(self, timeout: float = 4.0) -> list[dict]:
        # The twins are plumbing, not models anyone installed.
        from .. import ollama_cpu

        return [m for m in self._get("/api/tags", timeout).get("models", []) if not ollama_cpu.is_twin(m.get("name", ""))]

    def list_models(self, key: str = "", timeout: float = 4.0) -> list[str]:
        return sorted(m["name"] for m in self._tags(timeout))

    def installed(self) -> list[dict]:
        out = []
        for m in self._tags():
            det = m.get("details") or {}
            out.append({"name": m.get("name"), "size_gb": round((m.get("size") or 0) / 1e9, 2),
                        "parameters": det.get("parameter_size", ""), "family": det.get("family", ""),
                        "quantization": det.get("quantization_level", "")})
        return out

    def loaded(self) -> list[dict]:
        from .. import ollama_cpu

        return [{"name": ollama_cpu.logical(m.get("name") or ""), "size_gb": round((m.get("size") or 0) / 1e9, 2),
                 "vram_gb": round((m.get("size_vram") or 0) / 1e9, 2), "expires_at": m.get("expires_at", "")}
                for m in self._get("/api/ps").get("models", [])]

    def set_loaded(self, model: str, loaded: bool, timeout: float = 60.0) -> None:
        body = {"model": self._wire(model), "keep_alive": "10m" if loaded else 0}
        try:
            with self._client(timeout) as client:
                resp = client.post(self.root + "/api/generate", json=body)
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        if resp.status_code >= 400:
            raise classify_response(resp)

    def delete(self, model: str) -> None:
        try:
            with self._client(30.0) as client:
                resp = client.request("DELETE", self.root + "/api/delete", json={"model": model})
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        if resp.status_code >= 400:
            raise classify_response(resp)


# Tests replace this to route every provider to a fake server; nothing else should.
TRANSPORT: dict = {"transport": None}


def for_provider(provider) -> OpenAICompatibleAdapter:
    cls = OllamaAdapter if provider.adapter == "ollama" else OpenAICompatibleAdapter
    headers = {}
    if "openrouter.ai" in (provider.base_url or ""):
        headers = {"X-Title": "Jarvis"}
    return cls(provider.base_url, TRANSPORT["transport"], headers)
