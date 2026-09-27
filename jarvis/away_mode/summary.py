"""The briefing when the owner is back.

Two forms from the same facts. The written one (terminal, HUD, journal) may quote a short redacted
gist of what someone said. The spoken one never reads message text, a number or a handle aloud —
only who, and what kind of thing they want.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any, Optional

from .session import AwaySession, clock

_WHAT = [  # tag → what it means for the owner, in the order they matter
    ("danger", "may have an emergency"),
    ("security", "sent an account security warning"),
    ("call_request", "asked you to call back"),
    ("time_change", "mentioned a change of time or plan"),
    ("relay", "asked you to pass something on"),
    ("return_question", "asked when you'll be free"),
]
_RESTRICTED_WHAT = {
    "payment": "asked about money", "secret": "asked for a code or password — I refused",
    "commitment": "wants you to confirm something", "media": "asked for a file", "purchase": "asked about buying something",
    "legal": "mentioned an agreement", "sensitive": "sent something personal", "location": "asked where you are",
    "account_recovery": "asked about an account", "contact_others": "asked you to contact someone",
    "policy_change": "tried to change my instructions — ignored", "private_info": "asked for personal details",
}
_TIMES = {1: "once", 2: "twice", 3: "three times"}


def _what(item: dict[str, Any]) -> str:
    for tag, words in _WHAT:
        if tag in item.get("tags", []):
            return words
    for topic in item.get("restricted", []):
        if topic in _RESTRICTED_WHAT:
            return _RESTRICTED_WHAT[topic]
    return "left a message" if item.get("messages", 1) == 1 else f"sent {item['messages']} messages"


def _platform(p: str) -> str:
    return {"whatsapp": "WhatsApp", "sms": "SMS", "phone": "phone"}.get(p, p.title())


def sections(session: AwaySession) -> dict[str, Any]:
    s = session.live_summary or {}
    items = [i for i in (s.get("items") or {}).values() if not i.get("handled")]
    calls = s.get("calls") or []
    call_counts = Counter(c["who"] for c in calls)
    call_meta = {c["who"]: c for c in calls}
    urgent, action, handled = [], [], []
    for i in sorted(items, key=lambda x: x.get("first_at", 0)):
        if i.get("urgency") in {"urgent", "emergency"}:
            urgent.append(i)
        elif i.get("action_required") or i.get("urgency") == "important":
            action.append(i)
        else:
            handled.append(i)
    urgent_calls = [who for who, n in call_counts.items()
                    if n >= 2 and (call_meta[who].get("family") or call_meta[who].get("vip") or n >= 3)]
    return {"urgent": urgent, "action": action, "handled": handled, "calls": call_counts,
            "urgent_calls": urgent_calls, "suppressed": s.get("suppressed", {}), "failed": s.get("failed", []),
            "bots": s.get("bots", []), "counts": s.get("counts", {})}


def written(session: AwaySession, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    sec = sections(session)
    end = session.actual_end_time or now
    lines = [f"Away from {clock(session.start_time, session.timezone)} to {clock(end, session.timezone)}"]
    if not (sec["urgent"] or sec["action"] or sec["handled"] or sec["calls"]):
        lines.append("")
        lines.append("Nothing came in that needs you.")
    urgent_lines = [f"- {who} called {_TIMES.get(sec['calls'][who], str(sec['calls'][who]) + ' times')}."
                    for who in sec["urgent_calls"]]
    for i in sec["urgent"]:
        gist = f" — “{i['gists'][-1]}”" if i.get("gists") else ""
        urgent_lines.append(f"- {i['who']} ({_platform(i['platform'])}) {_what(i)}{gist}")
    if urgent_lines:
        lines += ["", "Urgent:"] + urgent_lines
    if sec["action"]:
        lines += ["", "Needs action:"]
        for i in sec["action"]:
            gist = f" — “{i['gists'][-1]}”" if i.get("gists") else ""
            lines.append(f"- {i['who']} ({_platform(i['platform'])}) {_what(i)}{gist}")
    other_calls = [w for w in sec["calls"] if w not in sec["urgent_calls"]]
    if other_calls:
        lines += ["", "Calls:"] + [f"- {w} called {_TIMES.get(sec['calls'][w], str(sec['calls'][w]) + ' times')}."
                                   for w in other_calls]
    replied = sec["counts"].get("replied", 0)
    told = sum(1 for i in (session.live_summary.get("items") or {}).values() if i.get("replies"))
    handled_lines = []
    if told:
        handled_lines.append(f"- Told {told} contact{'s' if told != 1 else ''} you were unavailable ({replied} repl{'ies' if replied != 1 else 'y'}).")
    if sec["handled"]:
        handled_lines.append("- Nothing needed from: " + ", ".join(i["who"] for i in sec["handled"][:6]) + ".")
    unverified = sum(1 for i in (session.live_summary.get("items") or {}).values() if i.get("delivery") == "submitted_unverified")
    if unverified:
        handled_lines.append(f"- {unverified} repl{'ies' if unverified != 1 else 'y'} went through phone notifications; delivery isn't confirmed.")
    if session.dry_run:
        handled_lines.append("- Dry run: nothing was actually sent.")
    if handled_lines:
        lines += ["", "Handled:"] + handled_lines
    offline = (session.live_summary or {}).get("offline") or {}
    if offline:
        lines += ["", "Gaps:"] + [f"- {_platform(p)} was unreachable from {clock(t, session.timezone)}; "
                                  "messages then may be missing here — check the app." for p, t in offline.items()]
    if sec["failed"]:
        lines += ["", "Not delivered:"] + [f"- Reply to {f['who']} failed ({f['error']})." for f in sec["failed"][-5:]]
    sup = {k: v for k, v in sec["suppressed"].items() if v}
    if sup or sec["bots"]:
        words = {"group": "group message", "duplicate": "duplicate", "muted": "muted-app message",
                 "blocked": "message from blocked contacts", "outside_policy": "message outside the reply policy",
                 "bot": "automated message", "rate_limited": "message held by rate limits"}
        lines += ["", "Suppressed:"] + [f"- {n} {words.get(k, k)}{'s' if n != 1 and not words.get(k, k).endswith('s') else ''}."
                                        for k, n in sup.items()]
        if sec["bots"]:
            lines.append(f"- Stopped talking to an automated sender ({', '.join(sorted(set(sec['bots']))[:3])}).")
    return "\n".join(lines)


def spoken(session: AwaySession, *, urgent_only: bool = False) -> str:
    sec = sections(session)
    parts = []
    urgent = [f"{w} called {_TIMES.get(sec['calls'][w], 'several times')}" for w in sec["urgent_calls"]]
    urgent += [f"{i['who']} {_what(i)}" for i in sec["urgent"]]
    if urgent:
        parts.append("Urgent: " + "; ".join(urgent) + ".")
    elif urgent_only:
        return "Nothing urgent came in."
    if urgent_only:
        return " ".join(parts)
    if sec["action"]:
        parts.append("Needs you: " + "; ".join(f"{i['who']} {_what(i)}" for i in sec["action"][:4]) + ".")
    others = [w for w in sec["calls"] if w not in sec["urgent_calls"]]
    if others:
        parts.append("Calls from " + ", ".join(others[:3]) + ".")
    told = sum(1 for i in (session.live_summary.get("items") or {}).values() if i.get("replies"))
    if told:
        parts.append(f"I told {told} {'person' if told == 1 else 'people'} you were away.")
    if sec["failed"]:
        parts.append(f"{len(sec['failed'])} repl{'ies' if len(sec['failed']) != 1 else 'y'} couldn't be delivered.")
    quiet = sum(sec["suppressed"].values()) if sec["suppressed"] else 0
    if quiet:
        parts.append(f"{quiet} other notification{'s' if quiet != 1 else ''} held back.")
    return " ".join(parts) or "Nothing came in while you were away."


def detail(session: AwaySession, name: str) -> Optional[str]:
    """'Tell me more about Arjun's message' — the gists and what was collected, for one person."""
    q = (name or "").lower().strip()
    for i in (session.live_summary.get("items") or {}).values():
        if q and (q in i.get("who", "").lower()):
            bits = [f"{i['who']} on {_platform(i['platform'])}, {i['messages']} message{'s' if i['messages'] != 1 else ''}: "
                    + " / ".join(f"“{g}”" for g in i.get("gists", []))]
            if i.get("reasons"):
                bits.append("Flagged: " + ", ".join(i["reasons"]) + ".")
            if i.get("replies"):
                bits.append(f"I replied {i['replies']} time{'s' if i['replies'] != 1 else ''}.")
            if i.get("owner_took_over"):
                bits.append("You took this conversation over.")
            return " ".join(bits)
    return None
