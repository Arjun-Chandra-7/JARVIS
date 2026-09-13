"""Calendar, Gmail and Tasks.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool


def google_status(ctx) -> str:
    """Why Google is unavailable, phrased so the model can relay a fix rather than a shrug."""
    from ..integrations.google.auth import status

    return status(ctx.config)

@tool("google_agenda", "Upcoming calendar events for N days.", {"days": {"type": "string"}},
          parallel_safe=True, requires="google")
async def google_agenda(ctx, a):
    from ..integrations.google import calendar as gcal
    return gcal.agenda(ctx.config, as_int(a.get("days"), 1) or 1) or google_status(ctx)

@tool("google_email_check", "List Gmail (default unread).", {"query": {"type": "string"}},
          parallel_safe=True, requires="google")
async def google_email_check(ctx, a):
    from ..integrations.google import gmail
    return gmail.check(ctx.config, a.get("query") or "is:unread") or google_status(ctx)

@tool("google_email_read", "Read the full body of one Gmail message by its id (from google_email_check).",
      {"id": {"type": "string"}}, ["id"],
          parallel_safe=True, requires="google")
async def google_email_read(ctx, a):
    from ..integrations.google import gmail
    return gmail.read(ctx.config, a.get("id", "")) or google_status(ctx)

@tool("google_email_send", "Send an email (confirm first).",
      {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["to", "subject", "body"],
          side_effects=True, requires="google")
async def google_email_send(ctx, a):
    if not await ctx.confirm(f"send an email to {a.get('to')} — {a.get('subject')}"):
        return "user declined."
    from ..integrations.google import gmail
    return gmail.send(ctx.config, a.get("to", ""), a.get("subject", ""), a.get("body", "")) or google_status(ctx)

@tool("google_calendar_create", "Create a calendar event. start/end are ISO datetimes.",
      {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
       "description": {"type": "string"}}, ["title", "start", "end"],
          side_effects=True, requires="google")
async def google_calendar_create(ctx, a):
    if not await ctx.confirm(f"create calendar event '{a.get('title')}' at {a.get('start')}"):
        return "user declined."
    from ..integrations.google import calendar as gcal
    return gcal.create_event(ctx.config, a.get("title", ""), a.get("start", ""), a.get("end", ""),
                             a.get("description", "")) or google_status(ctx)

@tool("google_tasks_list", "List your Google Tasks.", {},
          parallel_safe=True, requires="google")
async def google_tasks_list(ctx, a):
    from ..integrations.google import tasks
    return tasks.list_tasks(ctx.config) or google_status(ctx)

@tool("google_tasks_add", "Add a Google Task.", {"title": {"type": "string"}, "notes": {"type": "string"}}, ["title"],
          side_effects=True, requires="google")
async def google_tasks_add(ctx, a):
    from ..integrations.google import tasks
    return tasks.add_task(ctx.config, a.get("title", ""), a.get("notes", "")) or google_status(ctx)

@tool("google_tasks_complete", "Mark a Google Task complete by its title.", {"title": {"type": "string"}}, ["title"],
          side_effects=True, requires="google")
async def google_tasks_complete(ctx, a):
    from ..integrations.google import tasks
    return tasks.complete_task(ctx.config, a.get("title", "")) or google_status(ctx)
