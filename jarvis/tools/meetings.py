"""Meeting capture and the PA daemon.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool

@tool("start_pa_daemon", "Activate the PA guardian shield: monitors WhatsApp messages and phone calls while you're away, auto-replies on your behalf, and gives you a full debrief when you're back.", {},
          side_effects=True)
async def start_pa_daemon(ctx, a):
    from ..agent import pa_daemon
    pa_daemon.start(ctx.config)
    return "PA Guardian armed, sir. I'll handle all incoming messages and calls while you're out. I'll brief you the moment you're back."

@tool("stop_pa_daemon", "Deactivate the PA guardian and get a summary of what happened while you were away.", {},
          side_effects=True)
async def stop_pa_daemon(ctx, a):
    from ..agent import pa_daemon
    brief = pa_daemon.stop()
    return brief

@tool("whatsapp_scan", "Scan and analyse all WhatsApp contacts and recent chat history from the bridge.", {},
          side_effects=True)
async def whatsapp_scan(ctx, a):
    import httpx, os
    base = f"http://127.0.0.1:{os.environ.get('WA_PORT', '8765')}"
    try:
        status = httpx.get(f"{base}/status", timeout=3).json().get("connected", False)
        if not status:
            return "WhatsApp bridge is not connected. Start it with: cd ~/Dev/Jarvis/whatsapp && node wa_service.js"
        contacts_list = httpx.get(f"{base}/contacts", timeout=5).json()
        inbox_list = httpx.get(f"{base}/inbox", timeout=5).json()
    except Exception as exc:  # noqa: BLE001
        return f"Could not reach WhatsApp bridge: {exc}"
    by_sender: dict = {}
    for msg in inbox_list:
        name = msg.get("name") or msg.get("from", "?")
        by_sender.setdefault(name, []).append(msg.get("text", ""))
    lines = [f"WhatsApp Bridge — {len(contacts_list)} contacts known, {len(inbox_list)} recent messages.\n"]
    lines.append("Recent conversations:")
    for sender, texts in list(by_sender.items())[-10:]:
        last = texts[-1][:80] if texts else ""
        lines.append(f"  {sender} ({len(texts)} msg): \"{last}\"")
    if not by_sender:
        lines.append("  (No recent incoming messages in the bridge buffer.)")
    lines.append(f"\nTop contacts: {', '.join(c['name'] for c in contacts_list[:20] if c.get('name'))}")
    return "\n".join(lines)

@tool("join_meet_and_take_notes",
      "Join Google Meet with mic/camera off, record captions and participant changes. Omitted URL uses twa-pgjz-gss. Admission can require host approval; check status before claiming joined.",
      {"url": {"type": "string"}, "return_time": {"type": "string"}},
          side_effects=True)  # url not required — auto-detected
async def join_meet_and_take_notes(ctx, a):
    from ..integrations import meet_bot
    msg = await meet_bot.join_meet(a.get("url", "") or "", a.get("return_time", "soon"), ctx.config)
    st = meet_bot.status()
    return f"{msg} (state: {st['state']})"

@tool("stop_meet_notes", "Stop the Google Meet bot and retrieve the full meeting notes transcript.", {},
          side_effects=True)
async def stop_meet_notes(ctx, a):
    from ..integrations import meet_bot
    return await meet_bot.stop_meet()

@tool("toggle_sports_widget",
      "Opens or closes the live cricket sports widget on the screen.",
      {"state": {"type": "string", "description": "Must be exactly one of: on, off, toggle."}},
      ["state"],
          side_effects=True)
def toggle_sports_widget(ctx, a):
    """Toggles the sports widget visibility."""
    import urllib.request
    import json
    state = a.get("state", "toggle")
    try:
        # emit to the webserver so the overlay picks it up
        data = json.dumps({"kind": "sports_toggle", "text": state}).encode()
        req = urllib.request.Request("http://127.0.0.1:8770/emit", data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=2)
        return f"Sports widget {state}."
    except Exception as e:
        return f"Failed to toggle sports widget: {e}"
