"""Client for the local WhatsApp bridge (whatsapp/wa_service.js, via Baileys).

The Node service must be running and linked (scan the QR once). Jarvis talks to it over localhost.
"""

from __future__ import annotations

import os
import json
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


def smart_send(to: str, message: str) -> dict:
    """Send safely. Resolves a NAME to the right contact; if unknown/ambiguous it refuses and asks,
    rather than guessing a number (which is how messages went to strangers). Returns {ok, message}."""
    to = (to or "").strip()
    if not (message or "").strip():
        return {"ok": False, "message": "There's no message text to send."}
    if not status():
        return {"ok": False, "message": "WhatsApp bridge isn't running."}

    # 1) already a JID
    if "@" in to:
        r = send(to, message)
        return {"ok": bool(r.get("ok")), "message": "Sent." if r.get("ok") else f"Failed: {r.get('error')}"}

    # 2) a phone number (digits only, no letters)
    digits = "".join(c for c in to if c.isdigit() or c == "+")
    if digits and len(digits) >= 7 and not any(c.isalpha() for c in to):
        r = send(digits, message)
        return {"ok": bool(r.get("ok")), "message": "Sent." if r.get("ok") else f"Failed: {r.get('error')}"}

    # 3) a name → resolve in priority order:
    #    a) numbers Arjun explicitly told Jarvis (durable vault memory)
    #    b) the phone address book synced by KDE Connect (thousands of real contacts)
    #    c) the WhatsApp bridge's own learned contacts
    try:
        from . import contacts

        remembered = contacts.lookup(to)
        if remembered and remembered.get("number"):
            r = send(remembered["number"], message)
            if r.get("ok"):
                return {"ok": True, "message": f"Sent to {remembered['name']}."}
            return {"ok": False, "message": f"Couldn't send to {remembered['name']}: {r.get('error')}"}
    except Exception:  # noqa: BLE001
        pass

    try:
        from . import phone_contacts

        pcs = phone_contacts.lookup(to)
        exact = [c for c in pcs if c["name"].lower() == to.lower()]
        if len(pcs) == 1 or exact:
            target = exact[0] if exact else pcs[0]
            r = send(target["number"], message)
            if r.get("ok"):
                return {"ok": True, "message": f"Sent to {target['name']}."}
            return {"ok": False, "message": f"Couldn't send to {target['name']}: {r.get('error')}"}
        if len(pcs) > 1:
            names = ", ".join(c["name"] for c in pcs[:5])
            return {"ok": False, "message": f"Several contacts match '{to}': {names}. Which one?"}
    except Exception:  # noqa: BLE001
        pass

    cands = resolve(to)
    if not cands:
        return {"ok": False, "message": f"I don't have '{to}' in your contacts. Tell me their number "
                                        "(with country code) and I'll remember it."}
    exact = [c for c in cands if c.get("name", "").lower() == to.lower()]
    if len(cands) > 1 and not exact:
        names = ", ".join(c["name"] for c in cands[:5])
        return {"ok": False, "message": f"A few contacts match '{to}': {names}. Which one, or give me the number?"}
    target = exact[0] if exact else cands[0]
    r = send(target["jid"], message)
    if r.get("ok"):
        return {"ok": True, "message": f"Sent to {target['name']}."}
    return {"ok": False, "message": f"Couldn't send to {target['name']}: {r.get('error')}"}
