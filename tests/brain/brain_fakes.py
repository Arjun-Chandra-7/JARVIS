"""A fake provider server for Daily Brain tests. Fictional keys, fictional data, no network.

``FakeProviders`` is an ``httpx.MockTransport`` handler. Each model id maps to a behaviour:

    "text"                      answer with ``reply`` (or reply(body) when callable)
    ("status", 401, body?)      an HTTP error, optionally with a JSON body and headers
    ("timeout",)                raise a timeout
    ("offline",)                raise a connection error
    ("malformed",)              200 with a body that is not a chat completion
    ("tool", name, args)        a native tool call

Every request is recorded (host, path, model, key id) — never needed for assertions about
content, but used to prove *which* provider and key were asked and how many times.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

FAKE_GROQ_KEY = "fictional-groq-key-A"
FAKE_GROQ_KEY_2 = "fictional-groq-key-B"
FAKE_GEMINI_KEY = "fictional-gemini-key"


def fake_config(groq=True, gemini=True, **over):
    base = dict(groq_api_key=FAKE_GROQ_KEY if groq else "", gemini_api_key=FAKE_GEMINI_KEY if gemini else "",
                groq_model="openai/gpt-oss-120b", gemini_model="gemini-3.6-flash",
                ollama_base="http://ollama.test/v1", ollama_model="qwen2.5:3b", ollama_vision_model="moondream",
                brain="groq")
    base.update(over)
    return SimpleNamespace(**base)


class FakeProviders:
    def __init__(self):
        self.behaviour: dict[str, object] = {}
        self.calls: list[dict] = []
        self.models: dict[str, list[str]] = {}       # host -> listed model ids
        self.ollama_tags: list[dict] = []
        self.ollama_ps: list[dict] = []

    def set(self, model: str, behaviour) -> "FakeProviders":
        self.behaviour[model] = behaviour
        return self

    def count(self, model: str | None = None, host: str | None = None) -> int:
        return sum(1 for c in self.calls if (model is None or c["model"] == model)
                   and (host is None or c["host"] == host))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        auth = request.headers.get("authorization", "")
        if request.method == "GET" and path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": self.ollama_tags})
        if request.method == "GET" and path.endswith("/api/ps"):
            return httpx.Response(200, json={"models": self.ollama_ps})
        if request.method == "GET" and path.endswith("/models"):
            self.calls.append({"host": host, "path": path, "model": "", "auth": auth[-1:] if auth else ""})
            beh = self.behaviour.get(f"models@{host}")
            if isinstance(beh, tuple) and beh[0] == "status":
                return httpx.Response(beh[1], json=beh[2] if len(beh) > 2 else {"error": {"message": "x"}})
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models.get(host, [])]})
        body = json.loads(request.content or b"{}")
        model = body.get("model", "")
        self.calls.append({"host": host, "path": path, "model": model, "auth": auth[-1:] if auth else "",
                           "messages": body.get("messages", []), "tools": body.get("tools"),
                           "stream": body.get("stream", False)})
        beh = self.behaviour.get(model, "fake answer")
        if callable(beh):
            beh = beh(body)
        if isinstance(beh, str):
            return _completion(beh, body)
        kind = beh[0]
        if kind == "status":
            headers = beh[3] if len(beh) > 3 else {}
            return httpx.Response(beh[1], json=beh[2] if len(beh) > 2 else {"error": {"message": "error"}},
                                  headers=headers)
        if kind == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if kind == "offline":
            raise httpx.ConnectError("no route to host", request=request)
        if kind == "malformed":
            return httpx.Response(200, json={"unexpected": True})
        if kind == "tool":
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": beh[1], "arguments": json.dumps(beh[2])}}]}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}})
        if kind == "tools":
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                    {"id": f"call_{i}", "type": "function",
                     "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(beh[1])]}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}})
        raise AssertionError(f"unknown behaviour {beh!r}")


def _completion(text: str, body: dict) -> httpx.Response:
    prompt_tokens = sum(len(str(m.get("content", ""))) for m in body.get("messages", [])) // 4
    if body.get("stream"):
        chunks = []
        for i in range(0, len(text), 12):
            chunks.append("data: " + json.dumps({"choices": [{"delta": {"content": text[i:i + 12]}}]}))
        chunks.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": prompt_tokens,
                                                       "completion_tokens": len(text) // 4}}))
        chunks.append("data: [DONE]")
        return httpx.Response(200, text="\n\n".join(chunks), headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": text}}],
                                     "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": len(text) // 4}})
