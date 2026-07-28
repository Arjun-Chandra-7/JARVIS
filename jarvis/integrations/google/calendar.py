"""Google Calendar: read the agenda, create events. Returns None if Google isn't connected."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .auth import service


def agenda(config, days: int = 1):
    svc = service(config, "calendar", "v3")
    if svc is None:
        return None
    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=max(1, days))
    items = (
        svc.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=25,
        )
        .execute()
        .get("items", [])
    )
    if not items:
        return "Nothing on the calendar for that window."
    lines = []
    for event in items:
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "?")
        lines.append(f"- {start}  {event.get('summary', '(no title)')}")
    return "\n".join(lines)


def create_event(config, title: str, start: str, end: str, description: str = ""):
    svc = service(config, "calendar", "v3")
    if svc is None:
        return None
    body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    event = svc.events().insert(calendarId="primary", body=body).execute()
    return f"Created '{title}'. {event.get('htmlLink', '')}"
