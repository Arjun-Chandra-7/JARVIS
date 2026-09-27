"""The /brain HTTP API behind the overlay's Brain tab.

Two rules beyond the server's own (loopback only, Host and Origin checked — ``mobile.authorize``):

* **No secret ever leaves.** Key bodies are read as raw JSON rather than a pydantic model, because
  a validation error echoes the rejected input back; every response is built from
  ``KeyStore.public`` and redacted error details. A test scans every response for the key.
* **Only the owner's own machine changes anything.** Every mutating route refuses a request that
  arrived with a phone token or through a proxy (``_owner_only``), so neither a paired phone nor a
  message, webpage, email or tool output — which can reach Jarvis only as text — can add a key,
  switch a provider or change routing. There is no model tool for any of it either.
"""
from __future__ import annotations

import asyncio
import re
import time

from fastapi import APIRouter, HTTPException, Request

from . import adapters, local_models, router as brain_router, setup as brain_setup, telemetry, toolcheck
from .keys import KeyError_
from .registry import TEMPLATES

router = APIRouter(prefix="/brain", tags=["brain"])
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_MODEL = re.compile(r"^[A-Za-z0-9._:/@-]{1,120}$")


def _brain():
    from . import daily
    return daily.brain()


def _owner_only(request: Request) -> None:
    host = (request.client.host if request.client else "") or ""
    forwarded = any(h in request.headers for h in ("x-forwarded-for", "x-forwarded-host", "forwarded",
                                                   "tailscale-user-login"))
    if host not in {"127.0.0.1", "::1", "testclient"} or forwarded or request.headers.get("authorization"):
        raise HTTPException(403, "Brain settings can only be changed from this computer.")


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Expected a JSON object.") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected a JSON object.")
    return data


def _provider(pid: str):
    b = _brain()
    p = b.registry.providers.get(pid)
    if p is None:
        raise HTTPException(404, "No such provider.")
    return b, p


def _provider_view(b, p) -> dict:
    ks = b._keystore_or_none()
    d = p.to_public(ks.public(p.id, p.env_var) if not p.local or p.auth != "none" else [])
    d["key_storage"] = ks.backend.label
    d["discovered"] = b.registry.discovered(p.id)[:200]
    d["template"] = (b.registry.settings["providers"].get(p.id) or {}).get("template", p.id)
    d["base_url_editable"] = p.adapter == "openai_compatible" and p.id not in {"groq", "gemini"}
    for m in d["models"]:
        if not m["available"]:
            m["suggested_replacement"] = b.registry.suggest_replacement(m["ref"])
    return d


# --------------------------------------------------------------------------- read

@router.get("/overview")
async def overview():
    b = _brain()
    reg = b.registry
    routes = await asyncio.to_thread(brain_setup.route_table, reg)
    warnings = []
    for p in reg.providers.values():
        for m in p.models.values():
            if not m.available:
                warnings.append(f"{p.display_name}: {m.id} is no longer offered — pick a replacement.")
            elif m.health.startswith("paused"):
                warnings.append(f"{p.display_name}: {m.id} is {m.health.replace(':', ' (')})")
    for route, row in routes.items():
        if not row["ok"]:
            warnings.append(f"{route}: {row['why']}")
    recent = [{k: r.get(k) for k in ("ts", "route", "intent", "provider", "model", "fallback", "status",
                                     "latency_ms", "tokens_in", "tokens_out", "language")}
              for r in list(telemetry.RECENT)[-20:]][::-1]
    return {"profile": reg.settings["profile"], "profiles": brain_router.PROFILES, "routes": routes,
            "providers": [{"id": p.id, "name": p.display_name, "health": p.health, "local": p.local,
                           "enabled": p.enabled} for p in reg.providers.values()],
            "warnings": warnings[:12], "usage": telemetry.usage(500), "recent": recent,
            "offline": __import__("jarvis.brain.executor", fromlist=["offline"]).offline()}


@router.get("/providers")
async def providers():
    b = _brain()
    return {"providers": [_provider_view(b, p) for p in b.registry.providers.values()],
            "templates": {k: {"display_name": v["display_name"], "base_url": v["base_url"],
                              "key_hint": v["key_hint"], "local": v["local"]} for k, v in TEMPLATES.items()}}


@router.get("/routing")
async def routing():
    s = _brain().registry.settings
    return {"profile": s["profile"], "profiles": brain_router.PROFILES, "overrides": s["routing"],
            "limits": s["limits"], "privacy": s["privacy"],
            "routes": ["chat", "study", "research", "vision", "tools"],
            "models": [m.ref for m in _brain().registry.all_models() if "embedding" not in m.declared]}


@router.get("/usage")
async def usage():
    return telemetry.usage()


@router.get("/context")
async def context_debug():
    return _brain().last_context or {"report": None, "kept": [], "dropped": []}


@router.get("/setup")
async def setup_state():
    b = _brain()
    ollama = b.registry.providers.get("ollama")
    local = await asyncio.to_thread(local_models.inventory, adapters.for_provider(ollama), b.registry.state) \
        if ollama else {"reachable": False, "models": []}
    hw = await asyncio.to_thread(local_models.hardware)
    _note_local(b.registry, local)
    return brain_setup.state(b.registry, b._keystore_or_none(), local, hw)


def _note_local(registry, local: dict) -> None:
    """What Ollama actually has decides whether its configured models count as usable."""
    p = registry.providers.get("ollama")
    if p is None:
        return
    if local.get("reachable"):
        registry.record_catalogue("ollama", [m["name"] for m in local.get("models", [])])
    else:
        for m in p.models.values():                  # this view only; not written anywhere
            m.available, m.health = False, "unavailable"
        p.health = "unreachable"


@router.get("/local")
async def local():
    b = _brain()
    ollama = b.registry.providers.get("ollama")
    inv = await asyncio.to_thread(local_models.inventory, adapters.for_provider(ollama), b.registry.state)
    _note_local(b.registry, inv)
    return {"hardware": await asyncio.to_thread(local_models.hardware), **inv}


# --------------------------------------------------------------------------- providers

@router.post("/providers")
async def add_provider(request: Request):
    _owner_only(request)
    body = await _body(request)
    template = str(body.get("template", ""))
    if template not in TEMPLATES:
        raise HTTPException(400, "Unknown provider template.")
    pid = str(body.get("id") or template).lower()
    if not _ID.match(pid):
        raise HTTPException(400, "Provider id: lowercase letters, digits, - and _.")
    base = str(body.get("base_url") or TEMPLATES[template]["base_url"]).strip()
    if not re.match(r"^https?://[^\s]{3,200}$", base):
        raise HTTPException(400, "Base URL must start with http:// or https://")
    b = _brain()
    s = b.registry.settings
    if pid in b.registry.providers:
        raise HTTPException(409, "That provider already exists.")
    s["providers"][pid] = {"template": template, "base_url": base, "enabled": True,
                           "display_name": str(body.get("display_name") or TEMPLATES[template]["display_name"])[:40],
                           "local": bool(body.get("local", TEMPLATES[template]["local"])), "models": []}
    s.save()
    b.reload()
    return _provider_view(b, b.registry.providers[pid])


@router.patch("/providers/{pid}")
async def update_provider(pid: str, request: Request):
    _owner_only(request)
    body = await _body(request)
    b, p = _provider(pid)
    row = b.registry.settings["providers"].setdefault(pid, {})
    if "enabled" in body:
        row["enabled"] = bool(body["enabled"])
    if "priority" in body:
        row["priority"] = max(0, min(999, int(body["priority"])))
    if "base_url" in body:
        if p.id in {"groq", "gemini"} or p.adapter != "openai_compatible":
            raise HTTPException(400, "This provider's address is fixed.")
        if not re.match(r"^https?://[^\s]{3,200}$", str(body["base_url"])):
            raise HTTPException(400, "Base URL must start with http:// or https://")
        row["base_url"] = str(body["base_url"])
    b.registry.settings.save()
    b.reload()
    return _provider_view(b, b.registry.providers[pid])


@router.post("/providers/{pid}/test")
async def test_provider(pid: str, request: Request):
    """List the provider's models with its first usable key: connection + key + catalogue in one."""
    _owner_only(request)
    b, p = _provider(pid)
    ks = b._keystore_or_none()
    key = ""
    if not p.local and p.auth != "none":
        usable = ks.usable(p.id, p.env_var)
        if not usable:
            return {"ok": False, "kind": "no_key", "detail": "Add a key first."}
        key = ks.secret(usable[0])
    try:
        models = await asyncio.to_thread(adapters.for_provider(p).list_models, key, 8.0)
    except adapters.ProviderError as err:
        return {"ok": False, "kind": err.kind, "detail": err.detail}
    missing = b.registry.record_catalogue(pid, models)
    return {"ok": True, "models": len(models), "missing_configured": missing}


@router.post("/providers/{pid}/refresh")
async def refresh_catalogue(pid: str, request: Request):
    return await test_provider(pid, request)


@router.post("/providers/{pid}/models")
async def add_model(pid: str, request: Request):
    _owner_only(request)
    body = await _body(request)
    mid = str(body.get("model_id", "")).strip()
    if not _MODEL.match(mid):
        raise HTTPException(400, "That doesn't look like a model id.")
    b, p = _provider(pid)
    row = b.registry.settings["providers"].setdefault(pid, {})
    row["models"] = list(dict.fromkeys([*row.get("models", []), mid]))
    b.registry.settings.save()
    b.reload()
    return _provider_view(b, b.registry.providers[pid])


@router.delete("/providers/{pid}/models/{model_id:path}")
async def remove_model(pid: str, model_id: str, request: Request):
    _owner_only(request)
    b, p = _provider(pid)
    row = b.registry.settings["providers"].setdefault(pid, {})
    if model_id not in row.get("models", []):
        raise HTTPException(400, "Only manually added model ids can be removed here.")
    row["models"] = [m for m in row["models"] if m != model_id]
    b.registry.settings.save()
    b.reload()
    return _provider_view(b, b.registry.providers[pid])


@router.post("/models/validate")
async def validate_model(request: Request):
    """Run the tool/vision probes on one model. A handful of tiny requests; nothing is executed."""
    _owner_only(request)
    body = await _body(request)
    b = _brain()
    ref = str(body.get("ref", ""))
    if b.registry.model(ref) is None:
        raise HTTPException(404, "No such model.")
    return await asyncio.to_thread(toolcheck.validate_model, b.registry, b._keystore_or_none(), ref)


@router.post("/models/replace")
async def replace_model(request: Request):
    """The owner approves a replacement for a removed model. Nothing is replaced without this."""
    _owner_only(request)
    body = await _body(request)
    b = _brain()
    ref, new_id = str(body.get("ref", "")), str(body.get("new_id", ""))
    if b.registry.model(ref) is None or not _MODEL.match(new_id):
        raise HTTPException(400, "Unknown model or invalid replacement.")
    b.registry.approve_replacement(ref, new_id)
    b.reload()
    return {"ok": True}


# --------------------------------------------------------------------------- keys

def _key_error(exc: KeyError_):
    raise HTTPException(400, str(exc))


@router.post("/providers/{pid}/keys")
async def add_key(pid: str, request: Request):
    _owner_only(request)
    body = await _body(request)
    b, p = _provider(pid)
    if p.local and p.auth == "none" and pid == "ollama":
        raise HTTPException(400, "The local provider does not use keys.")
    secret = body.get("secret")
    if not isinstance(secret, str):
        raise HTTPException(400, "Paste the key as text.")
    try:
        row = b._keystore_or_none().add(pid, secret, str(body.get("label", ""))[:40])
    except KeyError_ as exc:
        _key_error(exc)
    finally:
        body.clear()
        del secret
    b.reload()
    return {"key": {k: row[k] for k in ("id", "label", "priority", "enabled", "fingerprint")},
            "provider": _provider_view(b, b.registry.providers.get(pid) or p)}


@router.patch("/providers/{pid}/keys/{kid}")
async def update_key(pid: str, kid: str, request: Request):
    _owner_only(request)
    body = await _body(request)
    b, p = _provider(pid)
    ks = b._keystore_or_none()
    try:
        if "label" in body:
            ks.rename(pid, kid, str(body["label"]))
        if "enabled" in body:
            ks.set_enabled(pid, kid, bool(body["enabled"]))
    except KeyError_ as exc:
        _key_error(exc)
    return _provider_view(b, p)


@router.post("/providers/{pid}/keys/order")
async def order_keys(pid: str, request: Request):
    _owner_only(request)
    body = await _body(request)
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise HTTPException(400, "Expected a list of key ids.")
    b, p = _provider(pid)
    try:
        b._keystore_or_none().reorder(pid, ids)
    except KeyError_ as exc:
        _key_error(exc)
    return _provider_view(b, p)


@router.post("/providers/{pid}/keys/{kid}/test")
async def test_key(pid: str, kid: str, request: Request):
    _owner_only(request)
    b, p = _provider(pid)
    try:
        return await asyncio.to_thread(b._keystore_or_none().test, pid, kid, adapters.for_provider(p), p.env_var)
    except KeyError_ as exc:
        _key_error(exc)


@router.post("/providers/{pid}/keys/{kid}/delete-request")
async def delete_key_request(pid: str, kid: str, request: Request):
    _owner_only(request)
    b, _ = _provider(pid)
    try:
        return {"confirm_token": b._keystore_or_none().request_delete(pid, kid), "expires_in_s": 120}
    except KeyError_ as exc:
        _key_error(exc)


@router.delete("/providers/{pid}/keys/{kid}")
async def delete_key(pid: str, kid: str, token: str, request: Request):
    _owner_only(request)
    b, p = _provider(pid)
    try:
        b._keystore_or_none().delete(pid, kid, token)
    except KeyError_ as exc:
        _key_error(exc)
    return _provider_view(b, p)


@router.post("/providers/{pid}/keys/migrate")
async def migrate_env_key(pid: str, request: Request):
    """Copy the provider's .env key into the keyring. The .env file is not modified."""
    _owner_only(request)
    b, p = _provider(pid)
    if not p.env_var:
        raise HTTPException(400, "This provider has no .env key.")
    try:
        b._keystore_or_none().migrate_env(pid, p.env_var)
    except KeyError_ as exc:
        _key_error(exc)
    return _provider_view(b, p)


# --------------------------------------------------------------------------- routing

@router.put("/routing")
async def set_routing(request: Request):
    _owner_only(request)
    body = await _body(request)
    b = _brain()
    s = b.registry.settings
    if "profile" in body:
        if body["profile"] not in brain_router.PROFILES:
            raise HTTPException(400, "Unknown profile.")
        s.data["profile"] = body["profile"]
    if "overrides" in body:
        ov = body["overrides"]
        if not isinstance(ov, dict):
            raise HTTPException(400, "overrides must be an object")
        known = {m.ref for m in b.registry.all_models()}
        clean = {}
        for prof, routes in ov.items():
            if prof not in brain_router.PROFILES or not isinstance(routes, dict):
                continue
            clean[prof] = {r: [ref for ref in refs if ref in known][:8] for r, refs in routes.items()
                           if r in {"chat", "study", "research", "vision", "tools"} and isinstance(refs, list)}
        s.data["routing"] = clean
    if "limits" in body and isinstance(body["limits"], dict):
        lim = body["limits"]
        for k, cast in (("max_cost_usd", float), ("max_latency_s", float)):
            if k in lim:
                s["limits"][k] = max(0.0, cast(lim[k]))
        for k in ("cloud_escalation", "external_vision"):
            if k in lim:
                s["limits"][k] = bool(lim[k])
    if "privacy" in body and isinstance(body["privacy"], dict):
        pv = body["privacy"]
        if pv.get("mode") in {"always_local", "prefer_local", "allow_cloud", "ask_before_cloud"}:
            s["privacy"]["mode"] = pv["mode"]
        if "allow_screenshots" in pv:
            s["privacy"]["allow_screenshots"] = bool(pv["allow_screenshots"])
        if "vision_providers" in pv:
            vp = pv["vision_providers"]
            s["privacy"]["vision_providers"] = None if vp is None else [str(x) for x in vp if str(x) in b.registry.providers]
    s.save()
    b.reload()
    return await routing()


@router.post("/setup/complete")
async def setup_complete(request: Request):
    _owner_only(request)
    b = _brain()
    b.registry.settings.data["setup_complete"] = True
    b.registry.settings.save()
    b.reload()
    return {"ok": True}


# --------------------------------------------------------------------------- local models

def _ollama():
    b = _brain()
    p = b.registry.providers.get("ollama")
    if p is None:
        raise HTTPException(404, "No local provider.")
    return b, adapters.for_provider(p)


@router.post("/local/{model:path}/load")
async def load_model(model: str, request: Request):
    _owner_only(request)
    _, a = _ollama()
    try:
        await asyncio.to_thread(a.set_loaded, model, True)
    except adapters.ProviderError as err:
        return {"ok": False, "kind": err.kind, "detail": err.detail}
    return {"ok": True}


@router.post("/local/{model:path}/unload")
async def unload_model(model: str, request: Request):
    _owner_only(request)
    _, a = _ollama()
    try:
        await asyncio.to_thread(a.set_loaded, model, False)
    except adapters.ProviderError as err:
        return {"ok": False, "kind": err.kind, "detail": err.detail}
    return {"ok": True}


@router.post("/local/{model:path}/benchmark")
async def benchmark_model(model: str, request: Request):
    _owner_only(request)
    b, a = _ollama()
    result = await asyncio.to_thread(local_models.benchmark, a, model)
    b.registry.state.data.setdefault("local_bench", {})[model] = result
    b.registry.state.save()
    return result


_REMOVE: dict[str, tuple[str, float]] = {}


@router.post("/local/{model:path}/delete-request")
async def remove_model_request(model: str, request: Request):
    _owner_only(request)
    import secrets
    token = secrets.token_urlsafe(8)
    _REMOVE[model] = (token, time.time() + 120)
    return {"confirm_token": token, "expires_in_s": 120}


@router.delete("/local/{model:path}")
async def remove_local_model(model: str, token: str, request: Request):
    _owner_only(request)
    want = _REMOVE.get(model)
    if not want or want[0] != token or want[1] < time.time():
        raise HTTPException(400, "Removing a model needs confirming.")
    _REMOVE.pop(model, None)
    _, a = _ollama()
    try:
        await asyncio.to_thread(a.delete, model)
    except adapters.ProviderError as err:
        return {"ok": False, "kind": err.kind, "detail": err.detail}
    return {"ok": True}
