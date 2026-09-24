"""The backend loop that feeds away mode — the only thing that replies to anyone.

Runs in the web/backend process beside the approval manager. Every few seconds, while an approved
session is running, it reads new WhatsApp messages from the bridge and hands each to the engine,
checks whether the owner has written in a conversation from the phone (which hands that thread to
them), ends the session at its planned time, drops idle thread state, and repeats alerts for
unacknowledged emergencies. Phone notifications and calls arrive over KDE Connect.

The voice process does not reply to anything; it only speaks the alerts queued here.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Optional

from .connectors import Connector, InboundMessage, PhoneNotificationConnector, WhatsAppConnector, mask
from .engine import AwayEngine
from .session import ACTIVE, EXPIRED, Store

logger = logging.getLogger("jarvis.away")

POLL_S = 3.0
OWNER_CHECK_S = 10.0
_MESSAGING_APPS = {"instagram": "instagram", "telegram": "telegram", "messages": "sms", "google messages": "sms",
                   "messaging": "sms", "sms": "sms", "signal": "signal", "messenger": "messenger", "snapchat": "snapchat"}

_state: dict[str, Any] = {"task": None, "engine": None, "connectors": {}, "kde": None}


def connector_for(config, platform: str) -> Optional[Connector]:
    conns = _state["connectors"]
    if platform == "whatsapp" and "whatsapp" not in conns:
        conns["whatsapp"] = WhatsAppConnector()
    return conns.get(platform)


def engine(config) -> AwayEngine:
    if _state["engine"] is None:
        connector_for(config, "whatsapp")
        _state["engine"] = AwayEngine(config, _state["connectors"])
    return _state["engine"]


def start(config) -> asyncio.Task:
    """Start the loop in the running event loop (idempotent). Raises RuntimeError without one."""
    task = _state["task"]
    if task is not None and not task.done():
        return task
    _state["task"] = asyncio.get_running_loop().create_task(run(config))
    return _state["task"]


def stop() -> None:
    task = _state["task"]
    if task is not None and not task.done():
        task.cancel()


def platform_of(app: str) -> Optional[str]:
    return _MESSAGING_APPS.get((app or "").strip().lower())


async def _watch_phone(config) -> None:
    """KDE Connect: messaging notifications and call events, while away."""
    try:
        from ..integrations.phone.kdeconnect import KDEConnect
        kde = await KDEConnect(getattr(config, "kde_device_id", "") or None).connect()
    except Exception as exc:  # noqa: BLE001 - the phone is optional
        logger.info("away: phone link unavailable (%s)", type(exc).__name__)
        return
    _state["kde"] = kde
    for platform in set(_MESSAGING_APPS.values()):
        _state["connectors"].setdefault(platform, PhoneNotificationConnector(platform, kde))
    eng, store = engine(config), Store(config)

    async def on_notification(note: dict) -> None:
        platform = platform_of(note.get("app", ""))
        if platform is None or store.active_session() is None:
            return
        title, text = str(note.get("title") or ""), str(note.get("text") or "")
        digest = hashlib.sha256(f"{title}|{text}".encode()).hexdigest()[:12]
        await eng.handle(InboundMessage(
            platform=platform, event_id=f"kde:{note.get('id')}:{digest}", thread_id=title or str(note.get("id")),
            sender_id=title, sender_name=title, text=text, reply_to=str(note.get("id") or "") if note.get("repliable") else "",
            is_group=":" in title and platform in {"telegram", "sms"}))

    def on_call(call: dict) -> None:
        event = str(call.get("event") or "")
        if event not in {"ringing", "missedCall"} or store.active_session() is None:
            return
        decision = eng.record_call("phone", str(call.get("number") or ""), str(call.get("name") or ""), event)
        logger.info("away: call from %s %s (%s)", mask(call.get("number", "")), event, decision.reason)
        session = store.active_session()
        if session and session.call_policy == "text_back" and event == "ringing" and call.get("number"):
            from .replies import call_text_back
            ok = kde.send_sms(str(call["number"]), call_text_back(session.owner_name, session.return_clock()))
            logger.info("away: text-back %s (delivery unverified)", "handed to phone" if ok else "failed")

    kde.watch_notifications(on_notification)
    try:
        kde.watch_calls(lambda c: asyncio.get_running_loop().call_soon(on_call, c))
    except Exception:  # noqa: BLE001
        pass
    while True:
        await asyncio.sleep(3600)


async def tick(config, eng: AwayEngine, store: Store, wa: Optional[WhatsAppConnector], seen: set[str],
               last_owner_check: list[float], now: Optional[float] = None) -> None:
    """One pass. Split out so tests can drive it without a loop or a bridge."""
    now = time.time() if now is None else now
    session = store.session()
    if session is None or session.status != ACTIVE:
        return
    if session.expired_at(now):
        from . import control
        ended = control.end(config, status=EXPIRED, store=store, now=now)
        if ended is not None:
            _announce_end(config, store, ended)
        return
    if wa is not None:
        if not await asyncio.to_thread(wa.available):
            _note_offline(store, "whatsapp", now)
        else:
            for msg in await asyncio.to_thread(wa.inbox):
                if msg.event_id and msg.event_id in seen:
                    continue
                seen.add(msg.event_id)
                await eng.handle(msg)
            if now - last_owner_check[0] >= OWNER_CHECK_S:
                last_owner_check[0] = now
                await _owner_takeovers(eng, store, wa, session.start_time, seen)
    _expire_threads(store, now)
    for s, alert in eng.escalator.repeat_due(store, now):
        await asyncio.to_thread(eng.escalator.deliver, s, alert)


async def _owner_takeovers(eng: AwayEngine, store: Store, wa: WhatsAppConnector, since: float, seen: set[str]) -> None:
    rows = await asyncio.to_thread(wa.owner_outgoing, since)
    state = store.read()
    for jid, mid, ts in rows:
        key = f"owner:{mid}"
        if not mid or mid in state["outbound"] or key in seen:
            continue
        seen.add(key)
        await eng.handle(InboundMessage(platform="whatsapp", event_id=f"owner-{mid}", thread_id=jid, sender_id="owner",
                                        sender_name="owner", text="", from_me=True, ts=ts))


def _note_offline(store: Store, platform: str, now: float) -> None:
    with store.edit() as state:
        raw = state.get("session")
        if not raw:
            return
        offline = raw["live_summary"].setdefault("offline", {})
        if platform not in offline:
            offline[platform] = now
            logger.info("away: %s connector offline", platform)


def _expire_threads(store: Store, now: float) -> None:
    state = store.read()
    ttl = float(state["prefs"].get("thread_ttl_s", 6 * 3600))
    stale = [k for k, t in state["threads"].items() if now - float(t.get("last_activity", now)) > ttl]
    if not stale:
        return
    with store.edit() as st:
        for k in stale:
            st["threads"].pop(k, None)   # the briefing item stays in the session summary


def _announce_end(config, store: Store, session) -> None:
    from . import escalation, summary
    with store.edit() as state:
        state["alerts"].append({"id": "end-" + session.session_id, "at": time.time(), "said": False, "level": "info",
                                "text": "Away mode has ended. " + summary.spoken(session, urgent_only=True)
                                + " Say what happened while I was away for the rest."})
    escalation.desktop("JARVIS: away mode ended", "Say “what happened while I was away” for the briefing.", False)


async def run(config) -> None:
    eng, store = engine(config), Store(config)
    wa = connector_for(config, "whatsapp")
    # Messages already in the bridge when this starts were either handled before a restart (their
    # ids are in the state file) or arrived before away mode; the engine tells which.
    seen: set[str] = set()
    last_owner_check = [0.0]
    phone = asyncio.create_task(_watch_phone(config))
    try:
        while True:
            try:
                await tick(config, eng, store, wa, seen, last_owner_check)
            except Exception as exc:  # noqa: BLE001 - one bad pass must not stop away mode
                logger.warning("away tick failed: %s", type(exc).__name__)
            if len(seen) > 5000:
                seen.clear()
            await asyncio.sleep(POLL_S)
    except asyncio.CancelledError:
        pass
    finally:
        phone.cancel()
