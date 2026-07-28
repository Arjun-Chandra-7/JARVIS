"""Client for the local WhatsApp bridge (whatsapp/wa_service.js, via Baileys).

The Node service must be running and linked (scan the QR once). Jarvis talks to it over localhost.
"""

from __future__ import annotations

import os

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

    # 3) a name → check Jarvis's own durable memory FIRST (numbers Arjun told it), then the bridge
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
