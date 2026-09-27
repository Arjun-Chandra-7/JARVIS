"""Capability requests from the rest of JARVIS — the one door to a model for subsystems.

The Study Companion, 3D Studio, contact summaries, away mode, meeting notes, omnicore and screen
vision each used to build their own client from ``Config.llm_params()``. They now describe what
they need and how private it is, and the Daily Brain decides who answers:

    CapabilityRequest(purpose="study.explanation", capabilities={"chat", "reasoning", "hinglish"},
                      privacy="personal", prompt=..., system=...)
        │
        ├─ every capability served by a registered local engine?  → ``engine`` route, no model
        │     (3D: blender_editing, vector/parametric reconstruction; nothing leaves the machine)
        ├─ privacy = max(declared floor, what privacy.classify finds in the words) — never lower
        ├─ router.plan: verified tool/vision/image-to-3D only, privacy policy, weak-model rules
        ├─ caps no model can have (image_to_3d, segmentation…) → refused, honestly
        ├─ local_only / cloud_needs_approval → cloud candidates dropped unless the owner named
        │     that provider in an approval (``approved_providers``); one provider's yes is not
        │     another's
        └─ executor.execute: no tools are ever passed, so nothing here can act

Telemetry gets the purpose, route, provider and privacy level — never the prompt or the answer.
With ``JARVIS_DAILY_BRAIN=0`` the previous single configured model answers, except that local-only
requests (contacts, anything marked secret) still never leave the machine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import executor, privacy as bprivacy, router, telemetry
from .request import BrainRequest, Cap, Intent, Privacy, RouteDecision, Source

# ------------------------------------------------------------------------------ local engines
# Capability -> the deterministic local engine that serves it. Registered by the subsystem that
# owns the engine (3D Studio registers its Blender bridge and reconstruction engines on import).
ENGINES: dict[str, str] = {}


def register_engine(capability: str, engine: str) -> None:
    ENGINES[capability] = engine


def engine_for(capabilities) -> Optional[str]:
    """The engine that serves *all* of these capabilities locally, or None."""
    caps = set(capabilities) - {Cap.CHAT, Cap.LOCAL}
    if not caps:
        return None
    engines = {ENGINES.get(c) for c in caps}
    if None in engines:
        return None
    return ",".join(sorted(e for e in engines if e))


# Capabilities the router itself treats as hard requirements; the rest of MUST_VERIFY is checked
# here, after planning.
_ROUTER_HARD = {Cap.TOOLS, Cap.VISION}
_NEVER_FROM_HERE = {Cap.TOOLS, Cap.RESEARCH}


@dataclass
class CapabilityRequest:
    purpose: str                                   # "study.explanation", "3d.scene_plan", …
    prompt: str
    system: str = ""
    capabilities: set = field(default_factory=lambda: {Cap.CHAT})
    privacy: str = Privacy.PUBLIC                  # a floor: classification may raise it, never lower
    source: str = Source.SYSTEM
    images: list = field(default_factory=list)     # data URIs; never logged
    screenshot: bool = False                       # the images are of the screen
    local_only: bool = False                       # never a cloud model, whatever the policy says
    cloud_needs_approval: bool = False             # cloud only for a provider named in an approval
    approved_providers: set = field(default_factory=set)
    intent: str = ""                               # default: study.* → study, images → vision, else chat
    language: str = ""                             # default: detected
    quality: str = "normal"                        # "high" asks for the strong tier
    max_tokens: int = 600
    temperature: float = 0.2
    deadline_s: float = 45.0
    json_mode: bool = False
    accept: Optional[Callable] = None              # measured check; a rejected fast answer escalates once
    correlation_id: str = ""


@dataclass
class CapabilityResult:
    ok: bool
    text: str = ""
    route: str = ""                                # "engine" | "local" | "cloud" | "" (not answered)
    provider: str = ""
    model: str = ""
    engine: str = ""
    notice: str = ""                               # the honest sentence, when it matters
    reason: str = ""                               # why not ok: brain_disabled, privacy, no_model, failed…
    privacy: str = Privacy.PUBLIC
    escalated: bool = False
    decision: Optional[RouteDecision] = None

    @property
    def local(self) -> bool:
        return self.route in {"engine", "local"}


def _max_privacy(a: str, b: str) -> str:
    order = Privacy.ORDER
    return order[max(order.index(a) if a in order else 0, order.index(b) if b in order else 0)]


def build(req: CapabilityRequest) -> BrainRequest:
    """The BrainRequest for ``req``: declared capabilities, a privacy level never below the floor."""
    from .understand import understand

    breq = understand(BrainRequest(req.prompt, source=req.source, authenticated=req.source in Source.TRUSTED,
                                   session_id=f"system:{req.purpose}", images=list(req.images),
                                   correlation_id=req.correlation_id),
                      asks_for_an_action=lambda _t: False)
    found = bprivacy.classify(req.prompt, req.source, has_images=bool(req.images), screen=req.screenshot)
    level = _max_privacy(_max_privacy(req.privacy, found.level), breq.privacy)
    breq.privacy = level
    breq.privacy_reasons = sorted(set(breq.privacy_reasons) | set(found.reasons) | {f"declared:{req.privacy}"})
    breq.intent = req.intent or (Intent.VISION if req.images else
                                 Intent.STUDY if req.purpose.startswith("study.") else Intent.CONVERSATION)
    caps = {c for c in req.capabilities if c not in _NEVER_FROM_HERE}
    if not req.images:
        caps.discard(Cap.VISION)
    if breq.intent != Intent.VISION:
        caps.add(Cap.CHAT)
    breq.capabilities = caps
    if req.language:
        breq.language = req.language
    breq.quality = req.quality
    if req.quality == "high":
        breq.capabilities.add(Cap.HIGH_ACCURACY)
        breq.capabilities.add(Cap.REASONING)
    breq.offline_required = bool(req.local_only)
    breq.output_tokens = req.max_tokens
    breq.deadline_s = req.deadline_s
    breq.context_budget = max(breq.context_budget, 12000)
    breq.tool_permission = "none"
    breq.side_effect_risk = "none"
    breq.fresh = False
    return breq


def plan(req: CapabilityRequest, registry) -> tuple[BrainRequest, RouteDecision]:
    breq = build(req)
    decision = router.plan(breq, registry)
    hard = {c for c in req.capabilities if c in Cap.MUST_VERIFY and c not in _ROUTER_HARD}
    kept = []
    for cand in decision.candidates:
        p = registry.providers[cand.provider_id]
        model = p.models.get(cand.model_id)
        if model is None or any(not model.has(c) for c in hard):
            decision.reasons.append(f"{cand.provider_id}/{cand.model_id}: {'/'.join(sorted(hard))} not verified")
            continue
        if not p.local and (req.local_only or breq.privacy == Privacy.SECRET):
            continue
        if not p.local and req.cloud_needs_approval and p.id not in req.approved_providers:
            decision.reasons.append(f"{p.id}: needs your approval naming it")
            continue
        kept.append(cand)
    decision.candidates = kept
    if not kept and not decision.refused:
        if hard:
            decision.refused = ("No model here can do " + ", ".join(sorted(c.replace("_", " ") for c in hard)) +
                                " — I'd need a provider that has been checked for it.")
        elif req.cloud_needs_approval and not req.local_only:
            decision.refused = "That needs a cloud model, and sending it needs your approval naming the provider."
        else:
            decision.refused = "No local model can do this well enough, and it isn't allowed to leave this machine."
    return breq, decision


def complete(req: CapabilityRequest, *, brain=None) -> CapabilityResult:
    """Answer ``req`` through the Daily Brain. Never raises; never acts."""
    engine = engine_for(req.capabilities)
    if engine:
        telemetry.record(purpose=req.purpose, route="engine", engine=engine, privacy=req.privacy,
                         capabilities=sorted(req.capabilities), status="engine_selected")
        return CapabilityResult(False, route="engine", engine=engine, reason="engine", privacy=req.privacy)
    from . import daily

    if not daily.enabled():
        return _legacy(req)
    try:
        b = brain or daily.brain()
        registry = b.registry
        breq, decision = plan(req, registry)
        if not decision.candidates:
            telemetry.from_decision(breq, decision, "refused", purpose=req.purpose, refused_kind="no_candidate")
            return CapabilityResult(False, notice=decision.refused, reason="no_model", privacy=breq.privacy,
                                    decision=decision)
        content = req.prompt
        if req.images:
            content = [{"type": "text", "text": req.prompt}] + [
                {"type": "image_url", "image_url": {"url": img}} for img in req.images]
        messages = ([{"role": "system", "content": req.system}] if req.system else []) + \
            [{"role": "user", "content": content}]
        res = executor.execute(breq, decision, messages, registry, b._keystore_or_none(),
                               json_mode=req.json_mode, temperature=req.temperature, accept=req.accept)
        status = "ok" if res.ok and not decision.quality_reduced else "degraded" if res.ok else "failed"
        telemetry.from_decision(breq, decision, status, purpose=req.purpose, escalated=res.escalated,
                                offline=executor.offline())
        if not res.ok:
            return CapabilityResult(False, notice=res.notice, reason="failed", privacy=breq.privacy,
                                    decision=decision)
        local = bool(registry.providers.get(decision.selected_provider) and
                     registry.providers[decision.selected_provider].local)
        return CapabilityResult(True, res.text, "local" if local else "cloud", decision.selected_provider,
                                decision.selected_model, notice=res.notice, privacy=breq.privacy,
                                escalated=res.escalated, decision=decision)
    except Exception as exc:  # noqa: BLE001 — a subsystem asking for words must never crash on the brain
        telemetry.record(purpose=req.purpose, route="capability", status=f"error:{type(exc).__name__}")
        return CapabilityResult(False, reason="error")


def _legacy(req: CapabilityRequest) -> CapabilityResult:
    """``JARVIS_DAILY_BRAIN=0``: the one configured model, as before — but a local-only or secret
    request still never goes to a cloud model, and images are not sent at all."""
    from .. import llm, providers as pv
    from ..config import CONFIG

    level = _max_privacy(req.privacy, bprivacy.classify(req.prompt, req.source).level)
    provider = pv.for_brain(CONFIG)
    is_local = "11434" in (provider.base_url or "") or "127.0.0.1" in (provider.base_url or "")
    if req.images:
        return CapabilityResult(False, reason="brain_disabled", privacy=level,
                                notice="Image understanding needs the Daily Brain, which is turned off.")
    if (req.local_only or level == Privacy.SECRET or req.cloud_needs_approval) and not is_local:
        return CapabilityResult(False, reason="privacy", privacy=level)
    started = time.monotonic()
    got = llm.complete_sync(req.system or "", req.prompt, CONFIG, temperature=req.temperature,
                            timeout=min(req.deadline_s, 60.0), ask=llm._ask)
    telemetry.record(purpose=req.purpose, route="legacy", privacy=level,
                     latency_ms=int((time.monotonic() - started) * 1000), status="ok" if got.ok else "failed")
    if not got.ok:
        return CapabilityResult(False, reason="failed", privacy=level, notice=got.unavailable_message())
    return CapabilityResult(True, got.text, "local" if is_local else "cloud", got.provider, privacy=level)
