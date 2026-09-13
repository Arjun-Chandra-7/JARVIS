"""OmniCore: recording communications and standing in as a PA.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool

@tool("get_activity_recordings", "Retrieve recent recorded text messages, calls, schedule alerts, and system activities.",
      {"limit": {"type": "integer"}, "category": {"type": "string"}},
          parallel_safe=True)
async def get_activity_recordings(ctx, a):
    from ..agent import omnicore
    return omnicore.list_recent_recordings(limit=as_int(a.get("limit", 15), 15), category_filter=a.get("category"), config=ctx.config)

@tool("check_pa_status", "Check Arjun's real-time computed schedule/busy status and whether 2-sided PA conversational defense is armed.", {},
          parallel_safe=True)
async def check_pa_status(ctx, a):
    from ..agent import omnicore
    status = omnicore.get_current_status(config=ctx.config)
    return f"Current Status: {'BUSY / AWAY (' + status.get('reason','') + ') until ' + str(status.get('until','')) if status.get('busy') else 'AVAILABLE'}. [Source: {status.get('source','Normal')}]"

@tool("set_pa_status", "Set Arjun's status manually (e.g., 'I am going out for 2 hours', 'In a meeting') or mark 'available'.",
      {"status_reason": {"type": "string"}, "is_busy": {"type": "boolean"}}, ["status_reason"],
          side_effects=True)
async def set_pa_status(ctx, a):
    # Single owner: away state + the WhatsApp auto-reply daemon are driven the same way
    # everywhere (voice command router, set_away tool, here).
    from ..agent import away, omnicore, pa_daemon
    if not as_bool(a.get("is_busy", True)) or a.get("status_reason", "").strip().lower() in ("available", "free", "back", "off"):
        away.set_available(ctx.config)
        omnicore.record_event("Status Change", "User", "Marked AVAILABLE / back at desk", config=ctx.config)
        return "Status updated to AVAILABLE. Welcome back, sir!"
    reason = a.get("status_reason", "Away from desk").strip()
    away.set_away(reason, ctx.config)
    pa_daemon.start(ctx.config)
    omnicore.record_event("Status Change", "User", f"Marked AWAY/BUSY: {reason}", config=ctx.config)
    return f"Status set to BUSY / AWAY ({reason}). I'll answer new WhatsApp messages as your assistant and brief you on return."

@tool("add_user_schedule", "Register a scheduled activity or event (like tuition or classes) with start and end ISO timestamps.",
      {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}, ["title", "start", "end"],
          side_effects=True)
async def add_user_schedule(ctx, a):
    from ..agent import omnicore
    return omnicore.add_schedule_event(a.get("title", ""), a.get("start", ""), a.get("end", ""), source="Agent Tool", config=ctx.config)

@tool("process_incoming_communication", "Record an incoming text/call for the away-mode debrief. Does NOT auto-reply — the away-mode daemon is the sole WhatsApp auto-responder.",
      {"type": {"type": "string"}, "sender": {"type": "string"}, "id": {"type": "string"}, "content": {"type": "string"}}, ["type", "sender", "content"],
          side_effects=True)
async def process_incoming_communication(ctx, a):
    # Passive recorder only. Automatic replies to incoming WhatsApp/calls are owned
    # exclusively by jarvis.agent.pa_daemon so there is never a second responder.
    from ..agent import away
    c_type = "call" if "call" in a.get("type", "text").lower() else "whatsapp"
    away.record_event(ctx.config, {
        "id": a.get("id", "") or f"{c_type}:{a.get('sender', '')}",
        "type": c_type, "sender": a.get("sender", "Unknown"),
        "jid": a.get("id", "") or a.get("sender", ""),
        "text": a.get("content", "") or ("Incoming call" if c_type == "call" else ""),
        "status": "recorded",
    })
    return f"Recorded {c_type} from {a.get('sender', 'Unknown')} for your away-mode debrief."
