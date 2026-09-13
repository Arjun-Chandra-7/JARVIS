"""Timers and reminders.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool

@tool("set_timer", "Countdown timer. seconds = total number of seconds (5 min = 300).",
      {"seconds": {"type": "string"}, "label": {"type": "string"}}, ["seconds"],
          side_effects=True)
async def set_timer(ctx, a):
    from ..jobs import timers
    tid = timers.set_timer(as_int(a.get("seconds"), 60), a.get("label", ""))
    return f"timer #{tid} set."

@tool("set_reminder", "Reminder at an ISO-8601 time (persists).",
      {"when": {"type": "string"}, "text": {"type": "string"}}, ["when", "text"],
          side_effects=True)
async def set_reminder(ctx, a):
    from ..jobs import reminders
    r = reminders.add(ctx.config.vault_path, a.get("when", ""), a.get("text", ""))
    return f"reminder #{r['id']} set for {a.get('when')}."
