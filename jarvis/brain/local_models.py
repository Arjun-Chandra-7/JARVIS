"""Local models: what this machine can run, what is installed, and what each is good for.

Nothing here downloads anything. Recommending a model is a sentence in the Brain tab; pulling one
is the owner's action (``ollama pull``), and anything over 2 GB is flagged as large.

The benchmark measures installed models on the tasks a local model is actually given — intent
classification, tool selection, short English / Hindi / Hinglish answers, a summary, a simple
study explanation, strict JSON — plus first-token latency, tokens per second and cold vs warm
start. Each check is a fixed, fictional prompt with a deterministic pass rule; results are stored
in brain-state.json and decide which local roles a model is recommended for. It only ever talks to
the local Ollama server.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from typing import Optional

from . import adapters


def hardware() -> dict:
    out = {"ram_gb": None, "ram_available_gb": None, "gpu": None, "vram_gb": None, "vram_used_gb": None, "cpus": None}
    try:
        info = dict(line.split(":", 1) for line in open("/proc/meminfo") if ":" in line)
        out["ram_gb"] = round(int(info["MemTotal"].split()[0]) / 1e6, 1)
        out["ram_available_gb"] = round(int(info["MemAvailable"].split()[0]) / 1e6, 1)
    except (OSError, KeyError, ValueError):
        pass
    try:
        import os
        out["cpus"] = os.cpu_count()
    except Exception:  # noqa: BLE001
        pass
    if shutil.which("nvidia-smi"):
        try:
            row = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                 timeout=4).stdout.strip().splitlines()[0]
            name, total, used = [x.strip() for x in row.split(",")]
            out.update(gpu=name, vram_gb=round(int(total) / 1024, 1), vram_used_gb=round(int(used) / 1024, 1))
        except Exception:  # noqa: BLE001
            pass
    return out


def estimate(size_gb: float) -> dict:
    """Rough resident cost of a GGUF model: weights plus ~20% for context and runtime."""
    need = round(size_gb * 1.2, 1)
    return {"ram_gb": need, "vram_gb": need, "large": size_gb > 2.0}


ROLE_TASKS = {
    "intent": ("Classify the request into exactly one word from [open_app, question, message, timer]. "
               "Request: 'can you open spotify'. Answer with the word only.",
               lambda t: t.strip().lower().strip(".`'\"") == "open_app"),
    "json": ('Return only JSON {"city": string, "days": integer} for: weather in Pune for 3 days.',
             lambda t: _json_ok(t, {"city", "days"})),
    "english": ("In one sentence: why do we see lightning before we hear thunder?",
                lambda t: bool(re.search(r"(?i)light.*(faster|speed)|sound.*slower", t))),
    "hindi": ("एक वाक्य में बताइए: पानी 100 डिग्री पर क्यों उबलता है?",
              lambda t: bool(re.search(r"[ऀ-ॿ]{3,}", t))),
    "hinglish": ("Ek line mein Hinglish mein batao: phone ki battery jaldi kyun khatam hoti hai?",
                 lambda t: bool(re.search(r"(?i)\b(hai|hoti|kyunki|jab|zyada|se)\b", t)) and not re.search(r"[ऀ-ॿ]", t)),
    "summary": ("Summarise in under 15 words: The meeting moved from Monday to Wednesday because the "
                "projector was broken, and lunch will be provided.",
                lambda t: len(t.split()) <= 20 and bool(re.search(r"(?i)wednesday", t))),
    "study": ("Class 10: state Ohm's law in one sentence.",
              lambda t: bool(re.search(r"(?i)current.*(proportional|directly)|V\s*=\s*IR", t))),
}


def _json_ok(text: str, keys: set) -> bool:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return bool(m) and keys <= set(json.loads(m.group(0)))
    except ValueError:
        return False


def benchmark(adapter: "adapters.OllamaAdapter", model: str, tasks: Optional[list] = None) -> dict:
    """Run the role tasks against one local model. Cold start is measured after an unload."""
    tasks = tasks or list(ROLE_TASKS)
    out = {"model": model, "roles": {}, "at": time.time()}
    try:
        adapter.set_loaded(model, False)
    except adapters.ProviderError:
        pass
    for i, name in enumerate(tasks):
        prompt, check = ROLE_TASKS[name]
        pieces: list[str] = []
        try:
            res = adapter.chat(model, [{"role": "user", "content": prompt}], "", max_tokens=120,
                               temperature=0.0, timeout=120.0, on_delta=pieces.append)
        except adapters.ProviderError as err:
            out["roles"][name] = {"ok": False, "error": err.kind}
            continue
        tokens = max(1, res.completion_tokens or len("".join(pieces)) // 4)
        gen_s = max(0.001, (res.total_ms - (res.first_token_ms or 0)) / 1000)
        row = {"ok": bool(check(res.text)), "first_token_ms": round(res.first_token_ms or res.total_ms),
               "total_ms": round(res.total_ms), "tokens_per_s": round(tokens / gen_s, 1)}
        if i == 0:
            out["cold_first_token_ms"] = row["first_token_ms"]
        out["roles"][name] = row
    warm = [r["first_token_ms"] for n, r in out["roles"].items() if r.get("ok") is not None and "first_token_ms" in r][1:]
    out["warm_first_token_ms"] = round(sum(warm) / len(warm)) if warm else None
    out["passed"] = [n for n, r in out["roles"].items() if r.get("ok")]
    return out


def recommend(results: dict[str, dict], sizes: dict[str, float]) -> dict:
    """The smallest model that passed each role."""
    roles = {}
    for role in ROLE_TASKS:
        passing = [m for m, r in results.items() if r.get("roles", {}).get(role, {}).get("ok")]
        if passing:
            roles[role] = min(passing, key=lambda m: sizes.get(m, 99))
    return roles


def inventory(adapter: "adapters.OllamaAdapter", state=None) -> dict:
    """Installed and loaded models with estimates; reachable=False when Ollama is not running."""
    try:
        installed = adapter.installed()
        loaded = {m["name"]: m for m in adapter.loaded()}
    except adapters.ProviderError as err:
        return {"reachable": False, "reason": err.kind, "models": []}
    bench = (state.data.get("local_bench", {}) if state is not None else {})
    models = []
    for m in installed:
        row = dict(m)
        row.update(estimate(m["size_gb"]))
        row["loaded"] = m["name"] in loaded
        row["vram_now_gb"] = loaded.get(m["name"], {}).get("vram_gb")
        b = bench.get(m["name"])
        row["benchmark"] = {"passed": b.get("passed", []), "warm_first_token_ms": b.get("warm_first_token_ms"),
                            "at": b.get("at")} if b else None
        from .registry import declared
        tier, caps, *_ = declared(m["name"])
        row["capabilities"] = sorted(caps | {"local_private"})
        row["recommended_for"] = _uses(caps, b)
        models.append(row)
    return {"reachable": True, "models": models}


def _uses(caps: set, bench: Optional[dict]) -> list[str]:
    if "embedding" in caps:
        return ["memory search embeddings"]
    if "vision" in caps and "chat" not in caps:
        return ["private image description (after the vision probe passes)"]
    uses = []
    passed = set((bench or {}).get("passed", []))
    if bench is None:
        return ["not benchmarked yet"]
    if {"intent", "json"} <= passed:
        uses.append("intent routing and structured extraction")
    if {"english", "summary"} <= passed:
        uses.append("offline / private short answers")
    if "hinglish" in passed:
        uses.append("offline Hinglish replies")
    return uses or ["not reliable for any local role in the benchmark"]
