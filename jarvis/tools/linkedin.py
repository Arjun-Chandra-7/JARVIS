"""LinkedIn Content Copilot — drafts, scheduling, stats and networking.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

import asyncio

from .base import as_bool, as_int, tool
from .comms import compose_message

@tool(
    "linkedin_stats",
    "Read the LinkedIn content dashboard and open it on screen. Use for any request about "
    "LinkedIn performance, publishing state, drafts, schedule, or overall progress.",
    {},
          parallel_safe=True)
async def linkedin_stats(ctx, a):
    from ..integrations import linkedin
    return linkedin.stats(open_gui=True)

@tool(
    "linkedin_top_ideas",
    "Show the ten freshest ranked public topics and offer to write a draft about a selected one.",
    {},
          side_effects=True)
async def linkedin_top_ideas(ctx, a):
    from ..integrations import linkedin
    return linkedin.top_ideas()

@tool(
    "linkedin_delete_scheduled",
    "Cancel a pending scheduled LinkedIn post only when the user explicitly asks to cancel or delete it.",
    {"position": {"type": "integer"}},
          side_effects=True)
async def linkedin_delete_scheduled(ctx, a):
    from ..integrations import linkedin
    return linkedin.delete_scheduled(as_int(a.get("position"), 1))

@tool(
    "linkedin_open_profile",
    "Open the user's own public LinkedIn profile stored in the copilot settings. Use only "
    "when they explicitly ask for the public profile page; publishing and management requests "
    "belong in linkedin_open_console.",
    {},
          side_effects=True)
async def linkedin_open_profile(ctx, a):
    from ..integrations import linkedin
    return linkedin.open_profile()

@tool(
    "linkedin_open_console",
    "Open a LinkedIn copilot screen: dashboard, approvals, calendar, network, analytics, "
    "profile, or settings.",
    {"view": {"type": "string"}},
          side_effects=True)
async def linkedin_open_console(ctx, a):
    from ..integrations import linkedin
    view = (a.get("view") or "dashboard").strip().lower()
    error = linkedin._ensure()
    if error:
        return error
    return f"Opened the {view} screen." if linkedin.open_console(view) else "I couldn't open a browser window."

@tool(
    "linkedin_pending_drafts",
    "List LinkedIn posts waiting for the user's approval.",
    {},
          parallel_safe=True)
async def linkedin_pending_drafts(ctx, a):
    from ..integrations import linkedin
    return linkedin.pending_drafts()

@tool(
    "linkedin_read_draft",
    "Read a queued LinkedIn post aloud in full before the user decides whether to approve it.",
    {"position": {"type": "integer"}},
          parallel_safe=True)
async def linkedin_read_draft(ctx, a):
    from ..integrations import linkedin
    return linkedin.read_draft(as_int(a.get("position"), 1))

@tool(
    "linkedin_approve_draft",
    "Approve a LinkedIn draft only after it was read aloud in this session; the backend rejects "
    "approval if the exact text hash changed.",
    {"position": {"type": "integer"}},
          side_effects=True)
async def linkedin_approve_draft(ctx, a):
    from ..integrations import linkedin
    return linkedin.approve_read_draft(as_int(a.get("position"), 1))

@tool(
    "linkedin_networking",
    "Read and open the small LinkedIn networking suggestion queue. The user handles every "
    "invitation manually.",
    {},
          side_effects=True)
async def linkedin_networking(ctx, a):
    from ..integrations import linkedin
    return linkedin.networking(open_gui=True)

@tool(
    "linkedin_capture_idea",
    "Turn an idea or described work into a LinkedIn draft for later review.",
    {
        "topic": {"type": "string"},
        "notes": {"type": "string"},
        "category": {"type": "string"},
    },
    ["topic"],
          side_effects=True)
async def linkedin_capture_idea(ctx, a):
    from ..integrations import linkedin
    return linkedin.capture_idea(
        a.get("topic", ""),
        a.get("notes", ""),
        (a.get("category") or "BUILD_LOG").upper(),
    )

@tool("find_contact", "Look up a person's WhatsApp contact by name (before sending).",
      {"name": {"type": "string"}}, ["name"],
          parallel_safe=True)
async def find_contact(ctx, a):
    from ..integrations import contacts, phone_contacts, whatsapp
    name = a.get("name", "")
    out = []
    local = contacts.lookup(name)
    if local:
        out.append(f"{local['name']}" + (f" ({local['number']})" if local.get("number") else "") + " [remembered]")
    for c in phone_contacts.lookup(name)[:6]:
        out.append(f"{c['name']} ({c['number']})")
    out += [c["name"] for c in whatsapp.resolve(name)[:4]]
    return "; ".join(out) if out else f"No contact matching '{name}'."

@tool("remember_contact",
      "Permanently remember a person's phone number (and optional note) in the vault, so you can "
      "message/call them later. Use whenever the user tells you someone's number or who someone is.",
      {"name": {"type": "string"}, "number": {"type": "string"}, "note": {"type": "string"}},
      ["name"],
          side_effects=True)
async def remember_contact(ctx, a):
    from ..integrations import contacts
    return contacts.remember(a.get("name", ""), a.get("number", ""), a.get("note", ""))["message"]

@tool("wifi_scan",
      "List nearby Wi-Fi networks and reveal the passwords THIS computer has already "
      "saved. Cannot recover passwords for networks the machine never joined.",
      {},
          parallel_safe=True)
async def wifi_scan(ctx, a):
    from ..integrations import wifi_scan as ws
    return await asyncio.to_thread(ws.report)

@tool("bluetooth_scan",
      "List nearby Bluetooth devices (phones/watches/earbuds) as a camera-free people "
      "signal, naming any bound to a person. Counts anonymous devices; cannot ID a "
      "randomised address.",
      {},
          parallel_safe=True)
async def bluetooth_scan(ctx, a):
    from ..presence import bluetooth
    return await asyncio.to_thread(bluetooth.report, ctx.config)

@tool("who_is_around",
      "Who is physically nearby right now: people located by the webcam (range + bearing), "
      "range-only acoustic contacts, and people identified by their devices. Says what is "
      "unknown rather than guessing.",
      {},
          parallel_safe=True)
async def who_is_around(ctx, a):
    from ..presence import service as presence
    return await asyncio.to_thread(presence.summary, ctx.config)

@tool("remember_device",
      "Bind a MAC address to a person so Jarvis can recognise them by their phone/watch "
      "even with no line of sight. Randomised (private) MACs are refused.",
      {"mac": {"type": "string"}, "person": {"type": "string"}}, ["mac", "person"],
          side_effects=True)
async def remember_device(ctx, a):
    from ..presence import identity
    return identity.remember(ctx.config, a.get("mac", ""), a.get("person", ""))["message"]

@tool("contact_context",
      "What Jarvis knows about a person from past WhatsApp chats: frequency, recurring topics, "
      "pending items, last interaction. Use before messaging or when the user asks about someone. "
      "Compact derived summary only — not raw history.",
      {"name": {"type": "string"}}, ["name"],
          parallel_safe=True)
async def contact_context(ctx, a):
    from ..memory import contacts_index
    out = await asyncio.to_thread(contacts_index.recall, ctx.config, a.get("name", ""))
    return out or f"No prior WhatsApp context for '{a.get('name', '')}'."

@tool("conversation_search",
      "Search your own past WhatsApp conversations for a topic and return the matching lines "
      "(who said what). Local, private. Use when the user asks 'what did X say about Y'.",
      {"query": {"type": "string"}}, ["query"],
          parallel_safe=True)
async def conversation_search(ctx, a):
    from ..memory import contacts_index
    rows = await asyncio.to_thread(
        lambda: contacts_index.search(ctx.config, a.get("query", ""), authorized=True, limit=12))
    if not rows:
        return "Nothing in your local conversation index matches that."
    return "\n".join(f"{r['name']}{' (you)' if r['from_me'] else ''}: {r['text']}" for r in rows)

@tool("message_person",
      "Message someone by INTENT — you give the person's name and what the message is ABOUT, and "
      "Jarvis composes a natural, friendly WhatsApp message and sends it. Use this for requests like "
      "'message Pradhuman about his health' (about='ask how his health is'). For exact dictated text "
      "use whatsapp_send instead.",
      {"name": {"type": "string"}, "about": {"type": "string"}}, ["name", "about"],
          side_effects=True)
async def message_person(ctx, a):
    from ..integrations import whatsapp
    name, about = a.get("name", ""), a.get("about", "")
    text = await compose_message(ctx.config, name, about)
    res = whatsapp.smart_send(name, text)
    if res.get("ok"):
        return f'Sent to {name}: "{text}"'
    return res["message"]

@tool("place_call", "Open the phone dialer for a number.", {"number": {"type": "string"}}, ["number"],
          side_effects=True)
async def place_call(ctx, a):
    from ..integrations import apps
    return "dialing." if apps.phone_call(a.get("number", ""), ctx.config.kde_device_id or None) else "couldn't call."

@tool("phone_mirror", "Mirror + control the phone on screen (scrcpy).", {},
          side_effects=True)
async def phone_mirror(ctx, a):
    from ..integrations import apps
    ok, msg = apps.phone_mirror()
    return msg

@tool("set_away", "Away mode ON — auto-reply to messages/calls.", {"reason": {"type": "string"}},
          side_effects=True)
async def set_away(ctx, a):
    from ..agent import away, pa_daemon
    away.set_away(a.get("reason", ""), ctx.config)
    pa_daemon.start(ctx.config)
    return "away mode on."

@tool("set_available", "Away mode OFF.", {},
          side_effects=True)
async def set_available(ctx, a):
    from ..agent import away
    away.set_available(ctx.config)
    return "away mode off."
