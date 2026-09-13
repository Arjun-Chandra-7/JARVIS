"""What Jarvis should offer to do right now, derived from what is actually happening.

`/suggestions` used to return six fixed strings. They were reasonable prompts, but they were the
same six at 3am with a dead battery as at 9am with eleven unread messages, which makes them
wallpaper — the user learns they carry no information and stops reading them.

These are built from state the system already collects: the WhatsApp bridge's inbox, the coding-job
manager, battery and thermals, presence, the hour of the day, and whether the memory pass or a
Google sign-in needs attention. Each candidate has a priority; the highest few win, so a failing
build outranks "catch me up" and a critical battery outranks everything.

Every probe is wrapped and cheap. A suggestion list is a nicety, so a slow or broken integration
must degrade to fewer suggestions, never to a slow endpoint or an error.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

# Shown when nothing specific is happening — the old fixed list, kept as the floor.
_BASELINE = [
    {"label": "Catch me up", "icon": "inbox", "priority": 20,
     "say": "What did I miss? Summarise messages, mail and anything important."},
    {"label": "My agenda", "icon": "calendar", "priority": 18,
     "say": "What's on my calendar today and what's my next meeting?"},
    {"label": "Read screen", "icon": "eye", "priority": 12,
     "say": "Take a screenshot and tell me what's on my screen."},
    {"label": "System status", "icon": "activity", "priority": 10,
     "say": "Give me a full system status report."},
    {"label": "Focus mode", "icon": "moon", "priority": 8,
     "say": "I'm going heads-down. Hold non-urgent notifications and cover my messages."},
]


def _unread_whatsapp(limit_age_s: float = 6 * 3600) -> list[dict]:
    """Recent inbound direct messages, excluding groups, newsletters and our own sends."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/inbox", timeout=1.5) as r:
            msgs = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - bridge down is normal
        return []
    cutoff = time.time() - limit_age_s
    out = []
    for m in msgs if isinstance(msgs, list) else []:
        frm = str(m.get("from", ""))
        if m.get("fromMe") or "@g.us" in frm or "@newsletter" in frm:
            continue
        ts = m.get("ts")
        # The bridge reports milliseconds in some builds and seconds in others.
        if isinstance(ts, (int, float)):
            ts = ts / 1000.0 if ts > 1e11 else float(ts)
            if ts < cutoff:
                continue
        out.append(m)
    return out


def _coding_trouble() -> list[dict]:
    try:
        from .integrations.coding_jobs import list_jobs

        jobs = list_jobs() or []
    except Exception:  # noqa: BLE001
        return []
    return [j for j in jobs if str(j.get("status", "")).lower() in {"failed", "error", "blocked"}]


def _battery() -> Optional[dict]:
    try:
        from .integrations import system_stats

        return (system_stats.snapshot() or {}).get("battery")
    except Exception:  # noqa: BLE001
        return None


def _people_nearby() -> int:
    try:
        from .presence import service as presence
        from .config import CONFIG

        return int((presence.snapshot(CONFIG) or {}).get("people", 0))
    except Exception:  # noqa: BLE001
        return 0


def _google_needs_auth(config) -> bool:
    try:
        from .integrations.google.auth import load_credentials

        return config.google_token_file.exists() and load_credentials(config) is None
    except Exception:  # noqa: BLE001
        return False


def _safely(probe, default):
    """Run a probe, substituting `default` if it fails.

    Each probe already guards its own integration, but they are edited independently and a
    suggestion list is a nicety: one broken sensor should cost one chip, not the whole endpoint.
    """
    try:
        return probe()
    except Exception:  # noqa: BLE001
        return default


def build(config, now: Optional[datetime] = None, limit: int = 6) -> list[dict]:
    """The suggestion chips for the HUD, most useful first."""
    now = now or datetime.now()
    hour = now.hour
    out: list[dict] = []

    # --- things that are wrong ------------------------------------------------------------
    batt = _safely(_battery, None)
    if batt and not batt.get("plugged") and (batt.get("percent") or 100) <= 15:
        out.append({
            "label": f"Battery {batt['percent']}%", "icon": "alert", "priority": 100,
            "say": "My battery is nearly flat — what's draining it and what should I close?",
        })

    failing = _safely(_coding_trouble, [])
    if failing:
        first = failing[0]
        label = "Build failed" if len(failing) == 1 else f"{len(failing)} builds failed"
        out.append({
            "label": label, "icon": "alert", "priority": 90,
            "say": f"The coding task '{first.get('prompt', 'a job')}' failed. Show me what went wrong.",
        })

    if _safely(lambda: _google_needs_auth(config), False):
        out.append({
            "label": "Reconnect Google", "icon": "alert", "priority": 85,
            "say": "My Google sign-in has expired. Remind me how to reconnect it.",
        })

    # --- things waiting for a reply -------------------------------------------------------
    unread = _safely(_unread_whatsapp, [])
    if unread:
        names = []
        for m in unread:
            n = (m.get("name") or "").strip()
            if n and n not in names:
                names.append(n)
        who = names[0] if len(names) == 1 else f"{len(unread)} people"
        out.append({
            "label": f"Reply to {who}", "icon": "message", "priority": 80,
            "say": "Read me my recent WhatsApp messages and help me reply.",
        })

    # --- time of day ----------------------------------------------------------------------
    if 5 <= hour < 11:
        out.append({"label": "Morning brief", "icon": "sun", "priority": 70,
                    "say": "Give me my morning briefing: weather, calendar, and anything I missed overnight."})
    elif 21 <= hour or hour < 3:
        out.append({"label": "Wrap up the day", "icon": "moon", "priority": 60,
                    "say": "Summarise what I did today and what's outstanding for tomorrow."})
    elif 11 <= hour < 14:
        out.append({"label": "What's next", "icon": "calendar", "priority": 55,
                    "say": "What's my next meeting and what should I be doing right now?"})

    # --- who's here -----------------------------------------------------------------------
    people = _safely(_people_nearby, 0)
    if people > 1:
        out.append({"label": f"{people} people here", "icon": "users", "priority": 30,
                    "say": "Who is around me right now?"})

    seen = {s["label"] for s in out}
    for base in _BASELINE:
        if base["label"] not in seen:
            out.append(base)

    out.sort(key=lambda s: -s["priority"])
    # `priority` is internal ranking, not something the HUD should render.
    return [{k: v for k, v in s.items() if k != "priority"} for s in out[:limit]]
