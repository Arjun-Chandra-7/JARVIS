"""Routing events: enough to answer "why did that go to the local model?", nothing private.

Fields are an allow-list (``FIELDS``). Anything else passed in is dropped, so a caller cannot leak
a prompt by adding a field. Strings are length-capped and passed through ``privacy.redact``.

Storage: ``$JARVIS_STATE_DIR/brain-events.jsonl``, rotated at ``MAX_BYTES`` with ``KEEP`` old files
(about 3 MB in all), plus the last 300 events in memory for the overlay.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

from .privacy import redact

FIELDS = ("ts", "request_id", "correlation_id", "source", "intent", "route", "tier", "capabilities",
          "language", "privacy", "provider", "model", "fallback", "fallback_reasons", "capability_lost",
          "quality_reduced", "escalated", "tokens_in", "tokens_out", "context_before", "context_after",
          "latency_ms", "cost_usd", "status", "commit", "refused_kind", "offline")
MAX_BYTES = 1_000_000
KEEP = 2
RECENT: deque = deque(maxlen=300)
_LOCK = threading.Lock()


def _path() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser() / "brain-events.jsonl"


def _commit() -> str:
    try:
        from .. import build_info
        return str(build_info.info().get("commit", ""))[:12]
    except Exception:  # noqa: BLE001
        return ""


def _clean(value):
    if isinstance(value, str):
        return redact(value)[:120]
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in list(value)[:12]]
    if isinstance(value, float):
        return round(value, 5)
    return value


def record(**fields) -> dict:
    row = {"ts": time.time(), "commit": _commit()}
    row.update({k: _clean(v) for k, v in fields.items() if k in FIELDS and v not in (None, "", [])})
    RECENT.append(row)
    path = _path()
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                for i in range(KEEP, 0, -1):
                    older = path.with_suffix(f".jsonl.{i}")
                    newer = path.with_suffix(f".jsonl.{i - 1}") if i > 1 else path
                    if newer.exists():
                        os.replace(newer, older)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.chmod(path, 0o600)
        except OSError:
            pass
    return row


def from_decision(req, decision, status: str, **extra) -> dict:
    return record(request_id=req.request_id, correlation_id=req.correlation_id, source=req.source,
                  intent=req.intent, route=decision.route, tier=decision.selected_tier
                  if decision.selected_tier is not None else decision.tier,
                  capabilities=sorted(req.capabilities), language=req.language, privacy=req.privacy,
                  provider=decision.selected_provider, model=decision.selected_model,
                  fallback=decision.fallback, fallback_reasons=decision.fallback_reasons,
                  capability_lost=decision.capability_lost, quality_reduced=decision.quality_reduced,
                  tokens_in=decision.tokens_in, tokens_out=decision.tokens_out,
                  context_before=decision.context_tokens_before, latency_ms=decision.latency_ms,
                  cost_usd=decision.cost_usd, status=status, **extra)


def events(limit: int = 2000) -> list[dict]:
    rows: list[dict] = []
    path = _path()
    for p in [path.with_suffix(f".jsonl.{i}") for i in range(KEEP, 0, -1)] + [path]:
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return rows[-limit:]


def usage(limit: int = 2000) -> dict:
    """Totals by provider/model, failure kinds, fallback rate — for the Usage view."""
    rows = events(limit)
    by = defaultdict(lambda: {"requests": 0, "ok": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                              "latency_ms": []})
    failures, routes = Counter(), Counter()
    fallbacks = 0
    for r in rows:
        routes[r.get("route", "?")] += 1
        if r.get("fallback"):
            fallbacks += 1
        for reason in r.get("fallback_reasons", []) or []:
            failures[reason.rsplit(": ", 1)[-1]] += 1
        if not r.get("provider"):
            continue
        row = by[f"{r['provider']}/{r.get('model', '')}"]
        row["requests"] += 1
        row["ok"] += r.get("status") in {"ok", "degraded"}
        row["tokens_in"] += int(r.get("tokens_in") or 0)
        row["tokens_out"] += int(r.get("tokens_out") or 0)
        row["cost_usd"] += float(r.get("cost_usd") or 0)
        if r.get("latency_ms"):
            row["latency_ms"].append(int(r["latency_ms"]))
    models = {}
    for ref, row in by.items():
        lat = sorted(row.pop("latency_ms"))
        row["p50_ms"] = lat[len(lat) // 2] if lat else None
        row["cost_usd"] = round(row["cost_usd"], 4)
        models[ref] = row
    return {"events": len(rows), "models": models, "failures": dict(failures), "routes": dict(routes),
            "fallback_rate": round(fallbacks / len(rows), 3) if rows else 0.0}
