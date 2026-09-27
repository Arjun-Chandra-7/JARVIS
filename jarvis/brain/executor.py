"""Calling the chosen models, falling back within a budget, and saying what happened.

Budget per request: at most ``MAX_ATTEMPTS`` provider calls in total, at most ``KEYS_PER_MODEL``
keys for one model, never past the request's deadline, and one escalation. A request is never sent
repeatedly through every key.

Per failure kind:

    auth_failed / permission_denied   quarantine that key; try the next key (budget permitting)
    rate_limited / quota_exhausted    back that key off for as long as the provider said; next key
    model_not_found                   pause the model (shared breaker); next candidate; the
                                      configured id is *not* rewritten (registry.suggest_replacement)
    network                           the machine is offline: every other cloud candidate is
                                      skipped for this request and for ``OFFLINE_HOLD_S``
    timeout / provider_outage         pause the model briefly (shared breaker); next candidate
    malformed / bad_request / other   next candidate; nothing is paused for long

Measured escalation: a caller may pass ``accept(result) -> reason | None``. When the answer from
a below-strong model fails that check, the next candidate of a *higher* tier is asked once. That
is an escalation, not a fallback, and is not announced.

Honesty: ``BrainResult.notice`` is the one sentence the person hears about any of this — only
when it matters (a fallback happened, quality may be reduced, or nothing could answer). It never
says "only the local model is available" unless every cloud candidate was actually tried or
checked in this request.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .. import providers as legacy
from . import adapters
from .adapters import ProviderError
from .keys import KeyStore
from .registry import Registry
from .request import BrainRequest, RouteDecision, Tier

MAX_ATTEMPTS = 4
KEYS_PER_MODEL = 2
OFFLINE_HOLD_S = 30.0
_OFFLINE = {"until": 0.0}

_LEGACY_KIND = {adapters.MODEL_GONE: legacy.MODEL_GONE, adapters.PERMISSION: legacy.PERMISSION,
                adapters.AUTH: legacy.AUTH, adapters.RATE_LIMIT: legacy.RATE_LIMIT,
                adapters.QUOTA: legacy.RATE_LIMIT, adapters.OUTAGE: legacy.OUTAGE,
                adapters.TIMEOUT: legacy.OUTAGE}

_PLAIN = {adapters.AUTH: "its key was rejected", adapters.PERMISSION: "access was denied",
          adapters.MODEL_GONE: "the model was removed", adapters.RATE_LIMIT: "it's rate-limited",
          adapters.QUOTA: "its quota is used up", adapters.OUTAGE: "it's down",
          adapters.TIMEOUT: "it timed out", adapters.NETWORK: "there's no internet",
          adapters.MALFORMED: "it sent back a broken reply", adapters.BAD_REQUEST: "it rejected the request",
          adapters.OTHER: "it failed", "paused": "it's paused after recent failures",
          "no_key": "no usable key is configured"}


def offline(now: float | None = None) -> bool:
    return (time.time() if now is None else now) < _OFFLINE["until"]


def mark_offline(now: float | None = None) -> None:
    _OFFLINE["until"] = (time.time() if now is None else now) + OFFLINE_HOLD_S


def mark_online() -> None:
    _OFFLINE["until"] = 0.0


@dataclass
class BrainResult:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    native_tool_calls: bool = False
    decision: Optional[RouteDecision] = None
    notice: str = ""
    ok: bool = False
    escalated: bool = False
    failures: list = field(default_factory=list)       # [(provider_id, model_id, kind)]


def _legacy_provider(registry: Registry, pid: str, mid: str) -> legacy.Provider:
    p = registry.providers[pid]
    return legacy.Provider(pid, p.base_url, "", mid, "strong")


def execute(req: BrainRequest, decision: RouteDecision, messages: list, registry: Registry,
            keystore: Optional[KeyStore], *, tools: list | None = None, json_mode: bool = False,
            temperature: float = 0.3, accept: Optional[Callable] = None, on_delta=None,
            adapter_for=None, now_fn=time.time, sleep=time.sleep) -> BrainResult:
    adapter_for = adapter_for or adapters.for_provider
    out = BrainResult(decision=decision)
    started = time.monotonic()
    deadline = started + req.deadline_s
    attempts = 0
    first = decision.candidates[0] if decision.candidates else None
    tried_cloud = 0
    skipped_offline = 0
    best_rejected: Optional[BrainResult] = None
    escalated_from_tier: Optional[int] = None

    for cand in decision.candidates:
        if attempts >= MAX_ATTEMPTS or time.monotonic() >= deadline:
            break
        if escalated_from_tier is not None and cand.tier <= escalated_from_tier:
            continue                              # an escalation only goes up
        p = registry.providers[cand.provider_id]
        model = p.models[cand.model_id]
        if not p.local and offline(now_fn()):
            skipped_offline += 1
            out.failures.append((cand.provider_id, cand.model_id, adapters.NETWORK))
            continue
        if legacy.blocked(_legacy_provider(registry, p.id, model.id), now_fn()):
            out.failures.append((cand.provider_id, cand.model_id, "paused"))
            continue
        if p.local or p.auth == "none":
            keys = [None]
        else:
            keys = keystore.usable(p.id, p.env_var) if keystore else []
            if not keys:
                out.failures.append((cand.provider_id, cand.model_id, "no_key"))
                continue
        adapter = adapter_for(p)
        for key in keys[:KEYS_PER_MODEL]:
            if attempts >= MAX_ATTEMPTS or time.monotonic() >= deadline:
                break
            attempts += 1
            tried_cloud += 0 if p.local else 1
            secret = keystore.secret(key) if key and keystore else ""
            remaining = max(3.0, deadline - time.monotonic())
            try:
                res = adapter.chat(model.id, messages, secret, tools=tools, max_tokens=req.output_tokens,
                                   temperature=temperature, timeout=min(remaining, 60.0), json_mode=json_mode,
                                   on_delta=on_delta if escalated_from_tier is None else None)
            except ProviderError as err:
                out.failures.append((cand.provider_id, cand.model_id, err.kind))
                _on_failure(err, registry, keystore, key, p.id, model.id, now_fn)
                if err.kind in adapters.KEY_FAULTS:
                    continue                     # a key problem: the next key may work
                break                             # a model/network problem: the next candidate
            if key and keystore:
                keystore.report_ok(key["id"])
            if not p.local:
                mark_online()
            legacy.record_success(_legacy_provider(registry, p.id, model.id))
            registry.state.note_latency(model.ref, res.first_token_ms, res.total_ms)
            registry.state.data["providers"].setdefault(p.id, {})["last_success"] = now_fn()
            registry.state.save()
            if not res.text and not res.tool_calls:
                out.failures.append((cand.provider_id, cand.model_id, adapters.MALFORMED))
                break
            reason = accept(res) if (accept and model.tier < Tier.STRONG) else None
            if reason:
                # Measured need: this answer failed its check. Keep it in case nothing better
                # answers, and ask a stronger model once.
                best_rejected = _fill(BrainResult(decision=decision), res, cand, model, first, out.failures)
                escalated_from_tier = model.tier
                decision.reasons.append(f"escalated: {reason}")
                break
            result = _fill(out, res, cand, model, first, out.failures)
            result.escalated = escalated_from_tier is not None
            result.decision.latency_ms = int((time.monotonic() - started) * 1000)
            result.notice = notice(req, result, registry, skipped_offline)
            return result

    if best_rejected is not None:
        best_rejected.failures = out.failures
        best_rejected.decision.quality_reduced = True
        best_rejected.decision.latency_ms = int((time.monotonic() - started) * 1000)
        best_rejected.notice = notice(req, best_rejected, registry, skipped_offline)
        return best_rejected
    decision.fallback_reasons = [f"{pid}/{mid}: {kind}" for pid, mid, kind in out.failures]
    decision.latency_ms = int((time.monotonic() - started) * 1000)
    out.notice = failure_notice(req, decision, out.failures, registry, skipped_offline)
    return out


def _on_failure(err: ProviderError, registry: Registry, keystore, key, pid: str, mid: str, now_fn) -> None:
    if err.kind == adapters.NETWORK:
        mark_offline(now_fn())
        return
    if key and keystore and err.kind in adapters.KEY_FAULTS:
        keystore.report(key["id"], err.kind, err.retry_after)
        return                                    # the key is at fault, not the model
    legacy_kind = _LEGACY_KIND.get(err.kind)
    if legacy_kind:
        legacy.record_failure(_legacy_provider(registry, pid, mid),
                              legacy.Failure(legacy_kind, err.detail, err.retry_after))
    if err.kind == adapters.MODEL_GONE:
        m = registry.providers[pid].models.get(mid)
        if m:
            m.available, m.health = False, "unavailable"
    registry.state.data["providers"].setdefault(pid, {})["last_failure"] = err.kind
    registry.state.save()


def _fill(out: BrainResult, res, cand, model, first, failures) -> BrainResult:
    d = out.decision
    out.text, out.tool_calls, out.native_tool_calls, out.ok = res.text, res.tool_calls, res.native_tool_calls, True
    d.selected_provider, d.selected_model, d.selected_tier = cand.provider_id, cand.model_id, cand.tier
    d.tokens_in, d.tokens_out = res.prompt_tokens, res.completion_tokens
    d.cost_usd = round((model.price_per_mtok or 0) * (res.prompt_tokens + res.completion_tokens) / 1e6, 6)
    d.fallback = bool(first and (first.provider_id, first.model_id) != (cand.provider_id, cand.model_id)
                      and any(f[:2] == (first.provider_id, first.model_id) for f in failures))
    d.fallback_reasons = [f"{pid}/{mid}: {kind}" for pid, mid, kind in failures]
    d.capability_lost = list(cand.lost)
    d.quality_reduced = cand.tier < d.tier or bool(cand.lost)
    d.ok = True
    return out


def _name(registry: Registry, pid: str) -> str:
    p = registry.providers.get(pid)
    return p.display_name.split(" (")[0] if p else pid


def notice(req: BrainRequest, result: BrainResult, registry: Registry, skipped_offline: int = 0) -> str:
    d = result.decision
    sel = registry.providers.get(d.selected_provider)
    if sel is None:
        return ""
    if sel.local and skipped_offline:
        return ("I'm offline. I can still control the computer, but this answer is using the smaller local model.")
    if not d.fallback:
        if sel.local and d.tier > Tier.LOCAL_FAST and d.route != "chat":
            return "This answer is from the smaller local model, so check anything important."
        return ""
    failed = [f for f in result.failures if f[0] != d.selected_provider]
    if not failed:
        return ""
    pid, _, kind = failed[0]
    why = _PLAIN.get(kind, kind)
    if sel.local:
        return (f"{_name(registry, pid)} is unavailable ({why}), so this answer is from the smaller local model "
                "— check anything important.")
    return f"{_name(registry, pid)} is unavailable ({why}), so I used the configured {_name(registry, sel.id)} fallback."


def failure_notice(req: BrainRequest, decision: RouteDecision, failures: list, registry: Registry,
                   skipped_offline: int) -> str:
    if decision.refused and decision.refused != "no_verified_tool_model":
        return decision.refused
    if not decision.candidates:
        return decision.refused or "No model is configured for this yet — open the Brain tab to set one up."
    if skipped_offline or any(f[2] == adapters.NETWORK for f in failures):
        local_tried = any(registry.providers[f[0]].local for f in failures if f[0] in registry.providers)
        if local_tried:
            return "I'm offline, and the local model couldn't answer either."
        return "I'm offline, and this needs a cloud model — I can still control the computer."
    seen, parts = set(), []
    for pid, _, kind in failures:
        if pid in seen:
            continue
        seen.add(pid)
        parts.append(f"{_name(registry, pid)}: {_PLAIN.get(kind, kind)}")
    return "I couldn't get an answer from any model right now (" + "; ".join(parts) + ")."
