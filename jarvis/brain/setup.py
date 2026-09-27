"""First run: find out what works, recommend the least that is enough, and say what is missing.

``state()`` is cheap and side-effect free (no model call): it reports whether any route has a
usable model, which providers have keys (never the keys), what the machine could run locally,
and a recommendation. The setup view in the Brain tab walks through it; saving keys and running
the short validation are separate, explicit steps (``/brain/keys``, ``/brain/validate``).

Jarvis works with any of: only a local model; one cloud provider; several; no vision provider; no
internet; no paid account. Deterministic commands never need any of it.
"""
from __future__ import annotations

from . import router
from .request import BrainRequest, Cap, Intent
from .understand import understand

PROBE_REQUESTS = {
    "chat": "Why is the sky blue?",
    "study": "Give me a 3 mark NCERT answer on Ohm's law",
    "research": "What's the latest news today?",
    "vision": "What is in this image?",
    "tools": "Message Rahul Verma saying I'll be late",
}


def route_table(registry) -> dict:
    """For each route: the model that would answer now, or why none would."""
    out = {}
    for route, text in PROBE_REQUESTS.items():
        req = understand(BrainRequest(text, images=["probe.png"] if route == "vision" else []))
        d = router.plan(req, registry)
        if d.candidates:
            c = d.candidates[0]
            out[route] = {"ok": True, "model": f"{c.provider_id}/{c.model_id}", "tier": c.tier,
                          "fallbacks": [f"{x.provider_id}/{x.model_id}" for x in d.candidates[1:4]]}
        else:
            out[route] = {"ok": False, "why": d.refused if d.refused != "no_verified_tool_model"
                          else "No model has passed the tool-calling check yet."}
    return out


def state(registry, keystore, local: dict | None = None, hardware: dict | None = None) -> dict:
    routes = route_table(registry)
    providers = []
    for p in registry.providers.values():
        keys = keystore.public(p.id, p.env_var) if keystore else []
        providers.append({"id": p.id, "name": p.display_name, "local": p.local, "enabled": p.enabled,
                          "health": p.health, "has_key": bool(keys) or p.local or p.auth == "none",
                          "models": len(p.models)})
    usable_chat = routes["chat"]["ok"]
    rec = recommend(registry, keystore, local or {}, hardware or {})
    missing = [f"{r}: {v['why']}" for r, v in routes.items() if not v["ok"]]
    return {"needs_setup": not usable_chat, "setup_complete": bool(registry.settings["setup_complete"]),
            "routes": routes, "providers": providers, "recommendation": rec, "unavailable": missing,
            "local": local or {}, "hardware": hardware or {}}


def recommend(registry, keystore, local: dict, hw: dict) -> list[str]:
    tips = []
    has_cloud = any(not p.local and (keystore.public(p.id, p.env_var) if keystore else [])
                    for p in registry.providers.values())
    if not has_cloud:
        tips.append("Add one free cloud key for everyday answers — Groq is fast, Gemini also sees images.")
    if not any(p.id == "gemini" for p in registry.providers.values()):
        tips.append("For screen and image questions, add a vision-capable provider (for example Gemini).")
    installed = [m for m in (local.get("models") or []) if "chat" in m.get("capabilities", [])]
    if local.get("reachable") and installed:
        smallest = min(installed, key=lambda m: m.get("size_gb", 99))
        tips.append(f"Keep {smallest['name']} for offline and private requests.")
    elif hw.get("ram_gb") and hw["ram_gb"] >= 8:
        tips.append("For offline use, install a small local model (about 2 GB, e.g. qwen2.5:3b) with Ollama.")
    tips.append("Run the short validation so tool use and vision are checked before they are relied on.")
    return tips
