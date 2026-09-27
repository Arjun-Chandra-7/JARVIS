"""Which models may answer a request, in which order, and why.

The hierarchy (docs/DAILY_BRAIN.md → Routing hierarchy):

    Tier 0  deterministic   fixed parsers in jarvis.commands — never reaches this module
    Tier 1  local-fast      small local model: offline, private, trivial
    Tier 2  cloud-fast      everyday questions, study explanations, tool selection, screen
    Tier 3  strong          measured difficulty, or the fast model's answer failed its check

Hard rules, applied before any preference:

* a capability in ``Cap.MUST_VERIFY`` (tool calling, vision) needs a *verified* model — declared
  is not enough, so a vision request can never reach a text-only model;
* input the privacy policy keeps local never reaches a cloud model;
* a weak local model is not a candidate for study, research, vision or hard reasoning unless the
  owner allowed it (``JARVIS_ALLOW_WEAK_TEACHING=1`` or the private profile) — an honest "I can't
  answer that well right now" beats a confident mistake;
* the owner's per-profile order (Brain tab → Routing) replaces the computed order where set.
"""
from __future__ import annotations

import os
from typing import Optional

from . import privacy
from .registry import ModelRecord, Registry
from .request import BrainRequest, Candidate, Cap, Intent, Privacy, RouteDecision, Tier

PROFILES = {
    # name: (description, desired tier for chat, allow local-first, strong-first)
    "fast": "Lowest latency: local model for trivial chat, fast cloud otherwise.",
    "balanced": "Fast cloud models for everyday questions; strong model when a task needs it.",
    "best": "Strongest configured model first for anything that needs a model.",
    "private": "Nothing leaves this machine. Local models only; limits stated plainly.",
    "study": "Study answers from the most accurate multilingual model; exam formats enforced.",
    "coding": "Code questions go to the strongest model with a long context.",
    "vision": "Screen and image questions go to verified vision models first.",
}

ROUTES = {Intent.STUDY: "study", Intent.RESEARCH: "research", Intent.VISION: "vision",
          Intent.ACTION: "tools", Intent.MEMORY: "tools"}


def route_for(req: BrainRequest) -> str:
    return ROUTES.get(req.intent, "chat")


def desired_tier(req: BrainRequest, route: str, profile: str) -> int:
    if profile == "private":
        return Tier.LOCAL_FAST
    if profile == "best":
        return Tier.STRONG
    if Cap.REASONING in req.capabilities or req.difficulty >= 0.6:
        return Tier.STRONG
    if route == "chat" and req.intent == Intent.CODE and profile == "coding":
        return Tier.STRONG
    if route == "study" and profile == "study":
        return Tier.STRONG
    if (profile == "fast" and route == "chat" and req.difficulty < 0.2 and req.language == "en"
            and req.intent == Intent.CONVERSATION):
        return Tier.LOCAL_FAST
    return Tier.CLOUD_FAST


def _weak_allowed(route: str, req: BrainRequest, profile: str) -> bool:
    if profile == "private" or req.offline_required:
        return True
    if os.environ.get("JARVIS_ALLOW_WEAK_TEACHING", "") in {"1", "true", "yes"}:
        return True
    return route == "chat" and req.difficulty < 0.6


def plan(req: BrainRequest, registry: Registry, profile: Optional[str] = None,
         now: Optional[float] = None) -> RouteDecision:
    settings = registry.settings
    profile = profile or settings["profile"]
    if profile not in PROFILES:
        profile = "balanced"
    route = route_for(req)
    decision = RouteDecision(req.request_id, route, desired_tier(req, route, profile))
    decision.reasons.append(f"profile={profile}")

    pv = settings["privacy"]
    verdict = privacy.cloud_verdict(req.privacy, "always_local" if profile == "private" else pv.get("mode", "allow_cloud"),
                                    images=bool(req.images), allow_screenshots=bool(pv.get("allow_screenshots", True)))
    cloud_ok = verdict.allowed and not req.offline_required
    if route == "vision" and not settings["limits"].get("external_vision", True):
        cloud_ok = False
        verdict.reason = verdict.reason or "external vision upload is turned off"
    if not cloud_ok:
        decision.reasons.append(f"local only: {verdict.reason or 'offline requested'}")
    vision_allow = pv.get("vision_providers")

    required = {c for c in req.capabilities if c in {Cap.TOOLS, Cap.VISION}}
    if route == "tools":
        required.add(Cap.TOOLS)
    if route == "vision":
        required.add(Cap.VISION)
    soft = {c for c in req.capabilities if c in {Cap.HINDI, Cap.HINGLISH, Cap.HIGH_ACCURACY, Cap.LONG_CONTEXT,
                                                 Cap.CODE, Cap.REASONING}}
    weak_ok = _weak_allowed(route, req, profile)
    limits = settings["limits"]
    rejected: list[str] = []

    pool: list[tuple[tuple, ModelRecord, list]] = []
    for m in registry.all_models():
        p = registry.providers[m.provider]
        ok, why = registry.usable(m, now)
        if not ok:
            rejected.append(f"{m.ref}: {why}")
            continue
        if "embedding" in m.declared or (not m.has(Cap.CHAT) and route != "vision"):
            continue
        if any(not m.has(c) for c in required):
            missing = [c for c in required if not m.has(c)]
            rejected.append(f"{m.ref}: {'/'.join(missing)} not verified")
            continue
        if not p.local and not cloud_ok:
            continue
        if route == "vision" and vision_allow is not None and not p.local and p.id not in vision_allow:
            rejected.append(f"{m.ref}: vision not allowed for this provider")
            continue
        # A local vision model that passed the vision probe may describe a picture; weak text
        # models may not stand in for study, research or hard reasoning.
        if m.tier == Tier.LOCAL_FAST and not weak_ok and not (route == "vision" and m.has(Cap.VISION)):
            rejected.append(f"{m.ref}: too weak for {route}")
            continue
        if m.completion_ms and limits.get("max_latency_s") and m.completion_ms > limits["max_latency_s"] * 1000:
            rejected.append(f"{m.ref}: slower than the latency limit")
            continue
        if m.price_per_mtok and limits.get("max_cost_usd") is not None:
            est = m.price_per_mtok * (req.context_budget + req.output_tokens) / 1e6
            if est > float(limits["max_cost_usd"]):
                rejected.append(f"{m.ref}: over the cost limit")
                continue
        if not limits.get("cloud_escalation", True) and m.tier == Tier.STRONG and decision.tier < Tier.STRONG \
                and not p.local:
            continue
        lost = sorted(c for c in soft if c not in m.declared)
        # Sort: nearest to the desired tier (above beats below), fewest lost soft capabilities,
        # prefer local when privacy asks for it, then the owner's provider priority, then speed.
        distance = m.tier - decision.tier
        tier_key = (0, distance) if distance >= 0 else (1, -distance)
        key = (tier_key, len(lost), 0 if (p.local and verdict.prefer_local) else 1, p.priority,
               m.completion_ms or 5000)
        pool.append((key, m, lost))

    pool.sort(key=lambda row: row[0])
    order = [(m, lost) for _, m, lost in pool]
    override = (settings["routing"].get(profile) or {}).get(route) or []
    if override:
        rank = {ref: i for i, ref in enumerate(override)}
        order.sort(key=lambda row: rank.get(row[0].ref, len(rank)))
        decision.reasons.append("owner's routing order")

    for m, lost in order:
        p = registry.providers[m.provider]
        why = "local — stays on this machine" if p.local else f"{Tier.NAMES[m.tier]} {route}"
        decision.candidates.append(Candidate(m.provider, m.id, m.tier, why, p.local, lost))
    if decision.candidates:
        first = decision.candidates[0]
        decision.requested = f"{Tier.NAMES[decision.tier]} via {first.provider_id}/{first.model_id}"
    else:
        decision.refused = _refusal(route, req, verdict, cloud_ok, rejected)
    decision.reasons.extend(rejected[:8])
    return decision


def _refusal(route: str, req: BrainRequest, verdict, cloud_ok: bool, rejected: list[str]) -> str:
    if route == "vision":
        return "No vision-capable model is available, so I can't reliably interpret that image."
    if route == "tools":
        return "no_verified_tool_model"
    if req.privacy == Privacy.SECRET:
        return ("That contains something that looks like a password or key, so it stays on this machine — "
                "and no local model is available to answer it here.")
    if not cloud_ok and verdict.reason:
        return f"I kept this on this machine because {verdict.reason}, and no local model can answer it well enough."
    if route in {"study", "research"} or req.difficulty >= 0.6:
        return ("None of the configured cloud models is available right now, and the local model isn't "
                "reliable enough for this — I'd rather not guess.")
    return "No model is configured or reachable right now."
