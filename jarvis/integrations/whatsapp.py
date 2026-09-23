"""Client for the local WhatsApp bridge (whatsapp/wa_service.js, via Baileys).

The Node service must be running and linked (scan the QR once). Jarvis talks to it over localhost.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

_BASE = f"http://127.0.0.1:{os.environ.get('WA_PORT', '8765')}"


def status() -> bool:
    try:
        return bool(httpx.get(f"{_BASE}/status", timeout=3).json().get("connected"))
    except Exception:  # noqa: BLE001
        return False


def send(to: str, text: str) -> dict:
    """Returns {'ok': True, 'jid': ...} or {'ok': False, 'error': ...}."""
    try:
        resp = httpx.post(f"{_BASE}/send", json={"to": to, "text": text}, timeout=25)
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def inbox() -> list[dict]:
    try:
        return httpx.get(f"{_BASE}/inbox", timeout=3).json()
    except Exception:  # noqa: BLE001
        return []


def chats(limit: int = 2000) -> list[dict]:
    """Read bridge history only. This endpoint never marks chats read or sends a message."""
    try:
        return httpx.get(f"{_BASE}/chats", params={"limit": max(1, min(int(limit), 5000))}, timeout=15).json()
    except Exception:  # noqa: BLE001
        return []


def import_context(config, limit: int = 2000) -> dict:
    """Persist WhatsApp history as local private context, without mutating WhatsApp.

    ``Jarvis/private/whatsapp-context.json`` is intentionally not surfaced in normal memory prompts;
    callers must explicitly retrieve it for a user-authorized recall operation.
    """
    records = chats(limit)
    if not isinstance(records, list):
        records = []
    target = Path(config.vault_path) / "Jarvis" / "private" / "whatsapp-context.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": "local WhatsApp bridge", "imported_at": datetime.now(timezone.utc).isoformat(), "records": records}
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(target.parent, 0o700)
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(target)
    return {"ok": True, "count": len(records), "path": str(target)}


def retrieve_imported_context(config, query: str, *, authorized: bool = False, limit: int = 20) -> list[dict]:
    """Search the private imported mirror for an explicitly authorized local-user request.

    This deliberately has no ambient access: callers must establish that the request came from the
    local owner before setting ``authorized=True``. It never sends, reads from WhatsApp, or exposes
    the full mirror by default.
    """
    if not authorized:
        return []
    terms = [term.lower() for term in str(query or "").split() if len(term) > 1][:8]
    if not terms:
        return []
    path = Path(config.vault_path) / "Jarvis" / "private" / "whatsapp-context.json"
    try:
        records = json.loads(path.read_text(encoding="utf-8")).get("records", [])
    except (OSError, ValueError, AttributeError):
        return []
    hits = []
    for record in reversed(records if isinstance(records, list) else []):
        if not isinstance(record, dict):
            continue
        haystack = " ".join(str(record.get(k, "")) for k in ("name", "from", "text")).lower()
        if all(term in haystack for term in terms):
            hits.append({k: record.get(k) for k in ("id", "name", "from", "text", "ts", "fromMe")})
            if len(hits) >= max(1, min(int(limit), 50)):
                break
    return hits


def contacts() -> list[dict]:
    """The WhatsApp address book the bridge has learned: [{jid, name}]."""
    try:
        return httpx.get(f"{_BASE}/contacts", timeout=3).json()
    except Exception:  # noqa: BLE001
        return []


def resolve(name: str) -> list[dict]:
    """Name → candidate contacts [{jid, name}] (exact matches first). Empty if none/unsure."""
    try:
        return httpx.get(f"{_BASE}/resolve", params={"name": name}, timeout=3).json()
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- sending
#
# Every outgoing WhatsApp message goes through here: resolve who, decide whether the owner has to
# see it first, send, check the bridge actually accepted it, and write down that it happened.
# "Sent" is said only after the bridge returns the chat it went to.

_PENDING_TTL_S = 180
_pending: dict = {}


def _approval_mode() -> str:
    """``new`` (default): preview the first message to someone not messaged before.
    ``always``: preview every message. ``never``: send once the recipient is certain."""
    mode = os.environ.get("JARVIS_SEND_APPROVAL", "new").strip().lower()
    return mode if mode in {"new", "always", "never"} else "new"


def _dry_run_forced() -> bool:
    return os.environ.get("JARVIS_DRY_RUN_SENDS", "").strip().lower() in {"1", "true", "yes", "on"}


def _audit_path() -> Path:
    from ..config import CONFIG
    return Path(CONFIG.vault_path) / "Jarvis" / "private" / "outbox.jsonl"


def _audit(status: str, name: str, address: str, message: str, error: str = "") -> None:
    """A record that a send happened, without the message in it: a hash and a length are enough
    to answer "did that go out?" and not enough to leak what it said."""
    import hashlib

    from .contacts import mask_number
    row = {"ts": datetime.now(timezone.utc).isoformat(), "platform": "whatsapp", "status": status,
           "to": name, "address": mask_number(address.split("@", 1)[0]),
           "key": hashlib.sha256(address.split("@", 1)[0].encode()).hexdigest()[:16],
           "sha256": hashlib.sha256(message.encode()).hexdigest()[:16], "chars": len(message)}
    if error:
        row["error"] = error[:200]
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass


def _messaged_before(address: str) -> bool:
    import hashlib

    key = hashlib.sha256(address.split("@", 1)[0].encode()).hexdigest()[:16]
    try:
        with open(_audit_path(), encoding="utf-8") as fh:
            return any(f'"key": "{key}"' in line and '"status": "sent"' in line for line in fh)
    except OSError:
        return False


def _is_known(cand) -> bool:
    # Someone the owner saved, has an existing WhatsApp chat with, or has messaged through Jarvis.
    return cand.source == "saved" or bool(cand.jid) or _messaged_before(cand.address)


def preview(cand, message: str) -> dict:
    from .contacts import mask_number
    return {"recipient": cand.name, "platform": "WhatsApp", "address": mask_number(cand.number or cand.jid),
            "message": message, "connector": "local WhatsApp bridge (Baileys)",
            "side_effect": f"sends one WhatsApp message to {cand.name}"}


def _deliver(cand, message: str) -> dict:
    if _dry_run_forced():
        _audit("dry_run", cand.name, cand.address, message)
        return {"ok": True, "status": "dry_run", "preview": preview(cand, message),
                "message": f'Dry run: would send to {cand.name} on WhatsApp: "{message}"'}
    if not status():
        return {"ok": False, "status": "failed",
                "message": "WhatsApp bridge isn't connected. Start it with: systemctl --user start jarvis-whatsapp"}
    r = send(cand.address, message)
    if r.get("ok") and r.get("jid"):
        _audit("sent", cand.name, cand.address, message)
        return {"ok": True, "status": "sent", "jid": r["jid"], "message": f"Sent to {cand.name} on WhatsApp."}
    error = str(r.get("error") or "the bridge did not confirm the send")
    _audit("failed", cand.name, cand.address, message, error)
    return {"ok": False, "status": "failed", "message": f"Couldn't send to {cand.name}: {error}"}


def smart_send(to: str, message: str, *, dry_run: bool = False, approved: bool = False) -> dict:
    """Send safely. Returns {ok, status, message, ...}.

    ``status`` is one of sent, dry_run, needs_approval, ambiguous, not_found, failed. A name is
    resolved through contacts.resolve and sent to only when one person clearly matches; anything
    less is a question back, because guessing is how messages went to strangers.
    """
    from . import contacts

    to = (to or "").strip()
    message = (message or "").strip()
    if not message:
        return {"ok": False, "status": "failed", "message": "There's no message text to send."}

    if "@" in to:                                                   # a JID, e.g. from the inbox
        cand = contacts.Candidate(name=to.split("@", 1)[0], jid=to, score=1.0, source="whatsapp")
    elif re.fullmatch(r"[+\d][\d\s()+-]{6,}", to):                   # a number
        cand = contacts.Candidate(name=contacts.mask_number(to), number=contacts.dialable(to),
                                  score=1.0, source="number")
    else:
        res = contacts.resolve(to, whatsapp_candidates=resolve)
        if not res.ok:
            return {"ok": False, "status": res.status, "message": res.question(),
                    "candidates": [c.name for c in res.candidates[:5]]}
        cand = res.best

    if dry_run:
        return {"ok": True, "status": "dry_run", "preview": preview(cand, message),
                "message": f'Dry run: would send to {cand.name} on WhatsApp: "{message}"'}
    mode = _approval_mode()
    if not approved and (mode == "always" or (mode == "new" and not _is_known(cand))):
        _pending.clear()
        _pending.update(cand=cand, message=message, at=time.monotonic())
        why = "" if mode == "always" else " You haven't messaged them through me before."
        return {"ok": False, "status": "needs_approval", "preview": preview(cand, message),
                "message": f'Ready to send to {cand.name} on WhatsApp: "{message}".{why} Say "send it" to confirm.'}
    return _deliver(cand, message)


def pending_send() -> dict | None:
    if _pending and time.monotonic() - _pending["at"] > _PENDING_TTL_S:
        _pending.clear()
    return dict(_pending) if _pending else None


def confirm_pending() -> dict:
    held = pending_send()
    _pending.clear()
    if not held:
        return {"ok": False, "status": "failed", "message": "There's no message waiting to be sent."}
    return _deliver(held["cand"], held["message"])


def cancel_pending() -> dict:
    held = pending_send()
    _pending.clear()
    return {"ok": True, "status": "cancelled",
            "message": f"Cancelled the message to {held['cand'].name}." if held else "Nothing was waiting to be sent."}
