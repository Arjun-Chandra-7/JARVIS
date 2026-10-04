"""Jarvis's local models run on the processor, so it stays inside its 0.75 GB of the GPU."""
import httpx

from jarvis import ollama_cpu
from jarvis.brain.adapters import OllamaAdapter
from jarvis.providers import Provider


def test_twin_names_round_trip():
    assert ollama_cpu.twin_name("qwen2.5:3b") == "qwen2.5:3b-jarvis-cpu"
    assert ollama_cpu.twin_name("moondream") == "moondream:latest-jarvis-cpu"
    assert ollama_cpu.twin_name("hf.co/org/model") == "hf.co/org/model:latest-jarvis-cpu"
    for name in ("qwen2.5:3b", "nomic-embed-text:latest"):
        assert ollama_cpu.logical(ollama_cpu.twin_name(name)) == name
    assert ollama_cpu.logical("moondream:latest-jarvis-cpu") == "moondream:latest"
    assert ollama_cpu.twin_name("qwen2.5:3b-jarvis-cpu") == "qwen2.5:3b-jarvis-cpu"


def test_twin_is_the_same_model_with_no_gpu_layers():
    sent = []
    name = ollama_cpu.wire("qwen2.5:3b", "http://localhost:11434/v1",
                           lambda url, body: sent.append((url, body)) or 200)
    assert name == "qwen2.5:3b-jarvis-cpu"
    assert sent == [("http://localhost:11434/api/create",
                     {"model": "qwen2.5:3b-jarvis-cpu", "from": "qwen2.5:3b",
                      "parameters": {"num_gpu": 0}, "stream": False})]
    # Made once per process, not per request.
    ollama_cpu.wire("qwen2.5:3b", "http://localhost:11434", lambda *_: sent.append(1) or 200)
    assert len(sent) == 1


def test_plain_name_when_the_twin_cannot_be_made():
    assert ollama_cpu.wire("qwen2.5:3b", post=lambda *_: 404) == "qwen2.5:3b"
    ollama_cpu.forget()

    def down(*_):
        raise httpx.ConnectError("refused")
    assert ollama_cpu.wire("qwen2.5:3b", post=down) == "qwen2.5:3b"


def test_opt_out(monkeypatch):
    monkeypatch.setenv("JARVIS_OLLAMA_GPU", "1")
    assert ollama_cpu.wire("qwen2.5:3b", post=lambda *_: 200) == "qwen2.5:3b"


def test_only_ollama_providers_are_renamed(monkeypatch):
    monkeypatch.setattr(ollama_cpu, "_default_post", lambda *_: 200)
    assert Provider("ollama", "http://localhost:11434/v1", "ollama", "qwen2.5:3b", "weak").wire_model \
        == "qwen2.5:3b-jarvis-cpu"
    assert Provider("groq", "https://api.groq.com/openai/v1", "k", "openai/gpt-oss-120b", "strong").wire_model \
        == "openai/gpt-oss-120b"


def test_adapter_chats_with_the_twin_and_hides_it_from_listings():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/create":
            return httpx.Response(200, json={"status": "success"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}, {"name": "qwen2.5:3b-jarvis-cpu"}]})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b-jarvis-cpu", "size_vram": 0}]})
        seen.append(httpx.Request.read(request))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]})

    a = OllamaAdapter("http://localhost:11434/v1", transport=httpx.MockTransport(handler))
    assert a.chat("qwen2.5:3b", [{"role": "user", "content": "hi"}]).text == "hi"
    assert b'"qwen2.5:3b-jarvis-cpu"' in seen[0]
    assert a.list_models() == ["qwen2.5:3b"]
    assert [m["name"] for m in a.loaded()] == ["qwen2.5:3b"]
