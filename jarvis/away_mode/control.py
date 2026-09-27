"""The owner's side of away mode: starting it (with approval), changing it, ending it, asking.

Starting is always a proposal through the shared approval manager: the policy is read back —
until when, which apps, who gets replies, how much Jarvis will say, what happens to calls — and
nothing is on until the owner says yes. The model's tools can propose; they cannot switch it on.

``handle(text, config, session_id)`` answers the owner's away-mode sentences and returns None for
anything else, so the command router can fall through.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from . import calls, intent, summary
from .engine import owner_name
from .escalation import acknowledge_all
from .people import Resolver, selector_for
from .session import ACTIVE, ENDED, EXPIRED, AwaySession, Store, blank_summary, clock, local_tz

_PLATFORM_NAMES = {"whatsapp": "WhatsApp", "instagram": "Instagram", "telegram": "Telegram", "sms": "SMS",
                   "email": "email", "messenger": "Messenger", "signal": "Signal", "snapchat": "Snapchat"}
# Platforms whose replies go through a connector at all. Everything else can only be muted or noted.
REPLYABLE = {"whatsapp", "instagram", "telegram", "sms", "messenger", "signal"}


def _names(platforms: list[str]) -> str:
    items = [_PLATFORM_NAMES.get(p, p) for p in platforms]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else "nothing"


# ---------------------------------------------------------------------------- lifecycle

def build_session(config, text: str, *, now: Optional[float] = None, created_from: str = "voice",
                  resolver: Optional[Resolver] = None) -> AwaySession:
    now = time.time() if now is None else now
    tz = local_tz()
    req = intent.parse_start(text, now, tz)
    resolver = resolver or Resolver()
    s = AwaySession(owner_name=owner_name(config), start_time=now, planned_end_time=req.end, timezone=tz,
                    created_from=created_from, reply_policy=req.reply_policy)
    s.allowed_platforms = [p for p in req.allowed_platforms if p in REPLYABLE] or ["whatsapp"]
    s.muted_platforms = req.muted_platforms
    s.allowed_contacts = [selector_for(n, resolver) for n in req.allowed]
    s.blocked_contacts = [selector_for(n, resolver) for n in req.blocked] + (["groupchat:work"] if req.block_work_groups else [])
    s.escalation_rules["interrupt"] = req.interrupt
    s.call_policy = "record"
    s.dry_run = _dry_run_default()
    return s


def _dry_run_default() -> bool:
    from .connectors import dry_run_forced
    return dry_run_forced()


def describe(s: AwaySession) -> str:
    """The policy, read back for approval."""
    until = f"until {s.return_clock()}" if s.planned_end_time else "until you stop it"
    who = "everyone" if not s.allowed_contacts else " and ".join(
        {"group:family": "family", "group:vip": "your VIPs"}.get(sel, sel.split(":", 1)[-1].replace("-", " ").title())
        for sel in s.allowed_contacts)
    blocked = [sel.split(":", 1)[-1].replace("-", " ").title() for sel in s.blocked_contacts if sel != "groupchat:work"]
    if "groupchat:work" in s.blocked_contacts:
        blocked.append("work groups")
    how = {"converse": "hold short conversations", "take_message": "only take messages",
           "silent": "not reply, only keep notes"}[s.reply_policy]
    parts = [f"turn on away mode {until}",
             f"I'll reply on {_names([p for p in s.allowed_platforms if p not in s.muted_platforms])} as JARVIS, "
             f"{s.owner_name}'s assistant, to {who}" + (f" except {', '.join(blocked)}" if blocked else "")
             + f", and {how}; group chats get no replies"]
    if s.muted_platforms:
        parts.append(f"{_names(s.muted_platforms)} stays silent")
    parts.append({"urgent_only": "I'll interrupt you only for urgent things",
                  "never": "I won't interrupt you except for an emergency",
                  "all": "I'll alert you for every new message"}[s.escalation_rules.get("interrupt", "urgent_only")])
    parts.append("I'll note who calls and alert you if someone keeps calling"
                 if not calls.capability_report()["can_answer"] else "I'll answer calls and take a message")
    if s.dry_run:
        parts.append("this is a dry run — nothing will actually be sent")
    return "; ".join(parts)


def propose_start(config, text: str, session_id: str = "local", *, created_from: str = "voice",
                  store: Optional[Store] = None) -> str:
    from ..approvals import MANAGER
    s = build_session(config, text, created_from=created_from)
    store = store or Store(config)
    summary_text = describe(s)
    action = MANAGER.propose("away", summary_text,
                             {"action": "away mode", "until": s.return_clock(), "platforms": ",".join(s.allowed_platforms),
                              "policy": s.reply_policy, "who": ",".join(s.allowed_contacts) or "everyone"},
                             lambda: activate(config, s, store=store), session=session_id)
    s.approval_id = action.id
    note = "" if intent.parse_until(text.lower(), time.time(), s.timezone) or intent.parse_duration(text.lower()) \
        else f" (You didn't say until when, so I've set {s.return_clock()}.)"
    return action.prompt() + note


def activate(config, s: AwaySession, *, store: Optional[Store] = None) -> dict[str, Any]:
    store = store or Store(config)
    now = time.time()
    if s.planned_end_time and s.planned_end_time <= now:
        return {"ok": False, "message": "That end time has already passed, so away mode is still off."}
    with store.edit() as state:
        old = state.get("session")
        if old and old.get("status") == ACTIVE:
            _archive(state, AwaySession.from_dict(old), ENDED, now)
        s.status = ACTIVE
        s.start_time = now
        s.live_summary = blank_summary()
        state["session"] = s.to_dict()
    try:
        from . import daemon
        daemon.start(config)
    except RuntimeError:
        pass   # no running loop (tests, a sync caller); the backend daemon picks the session up
    until = f" until {s.return_clock()}" if s.planned_end_time else ""
    return {"ok": True, "message": f"Away mode is on{until}. I'll answer as JARVIS, never as you, and brief you when you're back."}


def _archive(state: dict[str, Any], s: AwaySession, status: str, now: float) -> str:
    s.status = status
    s.actual_end_time = now
    for alert in s.pending_escalations:
        alert["acknowledged"] = True
    text = summary.written(s, now)
    state["archive"].append({"session_id": s.session_id, "ended": now, "status": status, "session": s.to_dict(),
                             "written": text, "spoken": summary.spoken(s)})
    state["session"] = s.to_dict()
    for a in state["alerts"]:
        a["said"] = True
    return text


def end(config, *, status: str = ENDED, store: Optional[Store] = None, now: Optional[float] = None) -> Optional[AwaySession]:
    """End the running session and archive its briefing. Returns it, or None if none was running."""
    store = store or Store(config)
    now = time.time() if now is None else now
    with store.edit() as state:
        raw = state.get("session")
        if not raw or raw.get("status") != ACTIVE:
            return None
        s = AwaySession.from_dict(raw)
        if status == EXPIRED and s.planned_end_time:
            now = min(now, s.planned_end_time)
        _archive(state, s, status, now)
    return s


def last_session(store: Store) -> Optional[AwaySession]:
    state = store.read()
    raw = state.get("session")
    if raw and raw.get("status") == ACTIVE:
        return AwaySession.from_dict(raw)
    if state["archive"]:
        return AwaySession.from_dict(state["archive"][-1]["session"])
    return AwaySession.from_dict(raw) if raw else None


def extend(config, text: str, *, store: Optional[Store] = None) -> str:
    store = store or Store(config)
    with store.edit() as state:
        raw = state.get("session")
        if not raw or raw.get("status") != ACTIVE:
            return "Away mode isn't on, so there's nothing to extend."
        s = AwaySession.from_dict(raw)
        new_end = intent.parse_extend(text, time.time(), s.timezone, s.planned_end_time)
        if new_end is None:
            return "Until when? Say something like “extend away mode by an hour” or “until 9”."
        if s.planned_end_time and new_end <= s.planned_end_time:
            return f"Away mode already runs until {s.return_clock()}."
        s.planned_end_time = new_end
        state["session"] = s.to_dict()
    return f"Extended — away mode now runs until {clock(new_end, s.timezone)}."


# ---------------------------------------------------------------------------- owner commands

_STATUS = re.compile(r"(?i)^(?:what'?s|what is) (?:happening|going on|up)(?: with my messages)?|^any (?:updates?|news)|"
                     r"^(?:away )?status$|^how'?s (?:it|everything) going")
_SUMMARIZE = re.compile(r"(?i)^(?:summari[sz]e|give me a summary of|read) (?:my |the )?messages(?: so far)?$")
_URGENT_ONLY = re.compile(r"(?i)^(?:read|tell me|give me|what are)(?: me)?(?: only)? (?:the )?urgent(?: ones| messages| things)?(?: only)?$|"
                          r"^only (?:the )?urgent(?: ones)?$|^anything urgent$")
_STOP_REPLYING = re.compile(r"(?i)^(?:stop|don'?t keep|quit) (?:replying|responding|answering|talking) to (?P<who>.+)$")
_TAKE_OVER = re.compile(r"(?i)^(?:i'?ll|i will|let me) (?:take (?:it )?over|handle|reply to|deal with) (?P<who>.+?)(?:'s)?(?: conversation| chat)?$|"
                        r"^take over (?:the |my )?(?P<who2>.+?)(?:'s)? (?:conversation|chat|thread)$|^take over$")
_REPLY_SAYING = re.compile(r"(?i)^(?:reply|respond|answer|tell)(?: to)?(?: (?P<who>[\w ]+?))? (?:saying|that|to say|with)[:,]? (?P<text>.+)$")
_MUTE = re.compile(r"(?i)^(?:mute|silence|ignore|stop handling) (?P<app>whatsapp|instagram|insta|telegram|sms|messenger|signal)(?: too| as well| for now)?$")
_MORE = re.compile(r"(?i)^(?:tell me more|more) about (?P<who>.+?)(?:'s|s')? (?:message|messages|call|calls|chat)$|"
                   r"^what did (?P<who2>.+?) (?:say|want)$")
_HANDLED = re.compile(r"(?i)^mark (?:that|it|this|(?P<who>.+?)(?:'s)?(?: message| one)?) (?:as )?(?:handled|done|read)$")
_SAVE = re.compile(r"(?i)^(?:save|keep|store) (?:this|that|the)(?: away)? (?:summary|briefing|debrief)(?: to my (?:notes|journal|vault))?$")
_DELETE = re.compile(r"(?i)^(?:delete|clear|erase|wipe|forget) (?:the |my |all )?(?:away[- ]?(?:mode|session)?) ?(?:history|summaries|records|data)$")
_VIP = re.compile(r"(?i)^(?:mark|add|make|set) (?P<who>.+?) (?:as (?:a )?vip|to (?:my )?vips?|a vip|as important)$")
_UNVIP = re.compile(r"(?i)^(?:remove) (?P<who>.+?) from (?:my )?vips?$")
_CALLS_Q = re.compile(r"(?i)^(?:can|could) you (?:answer|take|pick up|handle) (?:my )?(?:phone )?calls\??$")
_CLEAN = re.compile(r"(?i)^(?:hey\s+)?jarvis[,.!\s]*|[.!?]+$")


def _clean(text: str) -> str:
    return _CLEAN.sub("", (text or "").strip()).strip()


async def handle(text: str, config, session_id: str = "local", *, store: Optional[Store] = None) -> Optional[str]:
    said = _clean(text)
    if not said or (store is None and getattr(config, "vault_path", None) is None):
        return None        # no private vault, no away mode (a bare test or tool config)
    store = store or Store(config)
    state = store.read()
    raw = state.get("session")
    active = bool(raw and raw.get("status") == ACTIVE)
    have_history = active or bool(state["archive"])

    if intent.is_stop(said):
        if not active:
            return "Away mode isn't on." if re.search(r"(?i)away", said) else None
        s = end(config, store=store)
        return "Welcome back. Away mode is off. " + summary.spoken(s) if s else "Away mode is off."
    if active and intent.is_extend(said):
        return extend(config, said, store=store)
    if intent.is_start(said):
        if active:
            return f"Away mode is already on until {AwaySession.from_dict(raw).return_clock()}. Say “extend away mode” to change the time."
        return propose_start(config, said, session_id, store=store)
    if _CALLS_Q.match(said):
        return calls.capability_sentence()
    if intent.is_summary(said) or (have_history and _SUMMARIZE.match(said)):
        s = last_session(store)
        if s is None:
            return None     # never been away: "what did I miss" is the notification summary's
        acknowledge_all(store)
        return summary.spoken(s)
    if have_history and _URGENT_ONLY.match(said):
        s = last_session(store)
        acknowledge_all(store)
        return summary.spoken(s, urgent_only=True) if s else None
    if have_history and (m := _MORE.match(said)):
        s = last_session(store)
        who = m.group("who") or m.group("who2")
        return (summary.detail(s, who) if s else None) or f"I don't have anything from {who} in the away notes."
    if have_history and (m := _HANDLED.match(said)):
        return _mark_handled(store, m.group("who"))
    if have_history and _SAVE.match(said):
        return _save(config, store)
    if _DELETE.match(said):
        return _propose_delete(store, session_id)
    if m := _VIP.match(said):
        return _vip(store, m.group("who"), add=True)
    if m := _UNVIP.match(said):
        return _vip(store, m.group("who"), add=False)

    if not active:
        return None
    s = AwaySession.from_dict(raw)
    if _STATUS.match(said):
        acknowledge_all(store)
        live = summary.spoken(s)
        return f"Away mode runs until {s.return_clock()}. {live}" if s.planned_end_time else live
    if _SUMMARIZE.match(said):
        acknowledge_all(store)
        return summary.spoken(s)
    if m := _MUTE.match(said):
        app = {"insta": "instagram"}.get(m.group("app").lower(), m.group("app").lower())
        with store.edit() as st:
            cur = AwaySession.from_dict(st["session"])
            if app not in cur.muted_platforms:
                cur.muted_platforms.append(app)
            st["session"] = cur.to_dict()
        return f"{_PLATFORM_NAMES.get(app, app)} is muted for the rest of away mode."
    if m := _STOP_REPLYING.match(said):
        return _thread_action(store, m.group("who"), "blocked")
    if m := _TAKE_OVER.match(said):
        return _thread_action(store, m.group("who") or m.group("who2") or "", "owner")
    if m := _REPLY_SAYING.match(said):
        return _owner_reply(config, store, m.group("who") or "", m.group("text"), session_id)
    return None


def _find_threads(state: dict[str, Any], s: AwaySession, who: str) -> list[str]:
    q = re.sub(r"(?i)^(?:this|that|the|my)\s+|\s+(?:conversation|chat|thread|person)$", "", (who or "").strip()).lower()
    items = (s.live_summary.get("items") or {})
    if q in {"", "this", "that", "them", "him", "her", "it", "last", "latest"}:
        recent = sorted(items.values(), key=lambda i: i.get("last_at", 0))
        return [recent[-1]["thread"]] if recent else []
    return [k for k, i in items.items() if q and (q in i.get("who", "").lower())]


def _thread_action(store: Store, who: str, status: str) -> str:
    with store.edit() as state:
        s = AwaySession.from_dict(state["session"])
        keys = _find_threads(state, s, who)
        if not keys:
            return f"I haven't had a conversation with {who or 'anyone'} yet."
        names = []
        for key in keys:
            if key in state["threads"]:
                state["threads"][key]["status"] = status
            item = s.live_summary["items"].get(key, {})
            names.append(item.get("who", "them"))
            if status == "owner":
                if key not in s.taken_over:
                    s.taken_over.append(key)
                item["owner_took_over"] = True
            else:
                if key not in s.blocked_contacts:
                    s.blocked_contacts.append(key)
        state["session"] = s.to_dict()
    who_text = " and ".join(sorted(set(names)))
    return (f"Okay — it's yours. I've stopped replying to {who_text}." if status == "owner"
            else f"I'll stop replying to {who_text}.")


def _owner_reply(config, store: Store, who: str, text: str, session_id: str) -> str:
    """'Reply saying I'll call later': the owner's words, sent after a yes, marked as theirs."""
    state = store.read()
    s = AwaySession.from_dict(state["session"])
    keys = _find_threads(state, s, who)
    if not keys:
        return f"I don't have a conversation with {who or 'anyone'} to reply to."
    key = keys[-1]
    item = s.live_summary["items"].get(key, {})
    platform, thread_id = key.split(":", 1)
    body = text.strip().strip("\"'“”")
    owner = s.owner_name or owner_name(config)
    message = f"{owner} says: {body}"
    from ..approvals import MANAGER

    async def execute() -> dict[str, Any]:
        from .daemon import connector_for
        conn = connector_for(config, platform)
        if conn is None:
            return {"ok": False, "message": f"I can't reach {platform} right now."}
        result = await conn.send(thread_id, message)
        _thread_action(store, item.get("who", who), "owner")
        if result.verified:
            return {"ok": True, "message": f"Sent to {item.get('who', 'them')}."}
        if result.ok:
            return {"ok": True, "message": f"Handed to the phone for {item.get('who', 'them')}; I can't confirm delivery."}
        return {"ok": False, "message": f"Couldn't send to {item.get('who', 'them')}: {result.error}"}

    action = MANAGER.propose("message", f"send {item.get('who', 'them')} on {_PLATFORM_NAMES.get(platform, platform)}: “{message}”",
                             {"recipient": item.get("who", ""), "platform": platform, "action": "away owner reply",
                              "message": message}, execute, session=session_id)
    return action.prompt()


def _mark_handled(store: Store, who: Optional[str]) -> str:
    with store.edit() as state:
        target_raw = state["session"] if state.get("session") and state["session"].get("status") == ACTIVE else \
            (state["archive"][-1]["session"] if state["archive"] else None)
        if not target_raw:
            return "There's nothing to mark."
        s = AwaySession.from_dict(target_raw)
        items = s.live_summary.get("items") or {}
        open_items = [i for i in items.values() if not i.get("handled")]
        if who:
            open_items = [i for i in open_items if who.lower() in i.get("who", "").lower()]
        else:
            open_items = sorted(open_items, key=lambda i: ({"emergency": 3, "urgent": 2, "important": 1}.get(i.get("urgency"), 0),
                                                           i.get("last_at", 0)))[-1:]
        if not open_items:
            return "Nothing open matches that."
        for i in open_items:
            i["handled"] = True
        if target_raw is state.get("session"):
            state["session"] = s.to_dict()
        else:
            state["archive"][-1]["session"] = s.to_dict()
        names = ", ".join(i["who"] for i in open_items)
    return f"Marked {names} as handled."


def _save(config, store: Store) -> str:
    s = last_session(store)
    if s is None:
        return "There's no away summary to save."
    text = summary.written(s)
    try:
        from ..memory import vault
        vault.journal_append(config.vault_path, text)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't save it: {exc}"
    return "Saved the away summary to today's journal."


def _propose_delete(store: Store, session_id: str) -> str:
    from ..approvals import MANAGER

    def run() -> dict[str, Any]:
        n = store.clear_history()
        return {"ok": True, "message": f"Deleted the away-mode history ({n} record{'s' if n != 1 else ''})."}

    action = MANAGER.propose("away", "delete the away-mode history (threads, briefings and alerts)",
                             {"action": "delete away history"}, run, session=session_id)
    return action.prompt()


def _vip(store: Store, who: str, *, add: bool) -> str:
    sel = selector_for(who, Resolver())
    with store.edit() as state:
        vip = [v for v in state["prefs"].get("vip", []) if v != sel]
        if add:
            vip.append(sel)
        state["prefs"]["vip"] = vip
    name = who.strip().title()
    return f"{name} is a VIP now: their urgent messages and repeated calls will interrupt you." if add \
        else f"{name} is no longer a VIP."
