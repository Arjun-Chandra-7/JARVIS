"""Away-mode WhatsApp/call monitor.

Only direct incoming WhatsApp messages are eligible for an auto-reply. Incoming text never reaches
the main/tool-capable agent; ``away.respond`` is the isolated responder.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("jarvis.pa_daemon")
_task: Optional[asyncio.Task] = None
_seen_ids: set[str] = set()
_last_away_state = False
_last_debrief = ""
_active_config = None


def _message_id(msg: dict[str, Any]) -> str:
    return str(msg.get("id") or msg.get("messageId") or "|".join(str(msg.get(k, "")) for k in ("from", "ts", "text")))


def _eligible(msg: dict[str, Any]) -> bool:
    jid = str(msg.get("from") or "")
    return bool(jid and msg.get("text") and not msg.get("fromMe") and not msg.get("isGroup") and not msg.get("isNewsletter") and "@g.us" not in jid and "@newsletter" not in jid and jid != "status@broadcast")


def _claim_message(config, message_id: str) -> bool:
    """Atomically claim one message across daemon processes before any reply is sent."""
    digest = hashlib.sha256(message_id.encode("utf-8", "replace")).hexdigest()
    directory = config.vault_path / "Jarvis" / "private" / "away-claims"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / digest
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        # Do not risk a duplicate reply when durable coordination is unavailable.
        return False
    with os.fdopen(fd, "w") as claim:
        claim.write(message_id)
    return True


def _fetch_inbox() -> list[dict]:
    try:
        from ..integrations import whatsapp
        value = whatsapp.inbox()
        return value if isinstance(value, list) else []
    except Exception:  # noqa: BLE001
        return []


async def _handle_new_message(msg: dict, config) -> None:
    """Record and reply once; never invoke OmniCore or the main Jarvis agent."""
    from . import away
    from ..integrations import whatsapp
    mid = _message_id(msg)
    _seen_ids.add(mid)
    if not _claim_message(config, mid):
        logger.info("away mode skipped already-claimed WhatsApp message %s", mid)
        return
    sender, jid, text = str(msg.get("name") or "Unknown"), str(msg.get("from")), str(msg.get("text") or "")
    reply = await away.respond(config, jid, sender, text)
    status = "not_sent"
    if reply:
        result = whatsapp.send(jid, reply)
        status = "sent" if result.get("ok") else "send_failed"
    away.record_event(config, {"id": mid, "type": "whatsapp", "sender": sender, "jid": jid, "text": text, "reply": reply or "", "status": status})
    logger.info("away mode handled direct WhatsApp from %s (%s)", sender, status)


async def _handle_call(call: dict, config) -> None:
    from . import away
    caller, number = str(call.get("name") or call.get("number") or "Unknown Caller"), str(call.get("number") or "")
    away.record_event(config, {"id": f"call:{number}:{datetime.now().timestamp()}", "type": "call", "sender": caller, "jid": number, "text": "Incoming call", "status": "recorded"})


def events_for_debrief(config) -> list[dict]:
    from . import away
    return away.events(config)


def _generate_brief(config) -> str:
    entries = events_for_debrief(config)
    if not entries:
        return "All quiet while you were away — no direct messages or calls came in."
    lines = [f"Away-mode debrief — {len(entries)} item(s):"]
    for item in entries:
        when = str(item.get("at", ""))[11:16] or "?"
        if item.get("type") == "call":
            lines.append(f"- [{when}] Call from {item.get('sender', 'unknown')}.")
        else:
            lines.append(f"- [{when}] {item.get('sender', 'unknown')}: {str(item.get('text', ''))[:180]}")
            if item.get("reply"):
                lines.append(f"  Jarvis: {str(item['reply'])[:160]} ({item.get('status', 'unknown')})")
    return "\n".join(lines)


def latest_debrief(config) -> str:
    return _last_debrief or _generate_brief(config)


def debrief(config) -> str:
    """Public: a fresh away-mode debrief for the "what did I miss" command."""
    return _generate_brief(config)


async def _watch_calls_loop(config) -> None:
    try:
        from ..integrations.phone.kdeconnect import KDEConnect
        kc = await KDEConnect(config.kde_device_id or None).connect()
        queue: asyncio.Queue = asyncio.Queue()
        kc.watch_calls(lambda call: queue.put_nowait(call))
        while True:
            try:
                call = await asyncio.wait_for(queue.get(), timeout=5)
                from . import away
                if away.is_away(config):
                    await _handle_call(call, config)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
    except Exception as exc:  # noqa: BLE001
        logger.info("away mode call watch unavailable (%s)", exc)


async def run_pa_daemon(config) -> None:
    global _last_away_state, _last_debrief, _seen_ids, _active_config
    from . import away
    _active_config = config
    _seen_ids = {_message_id(msg) for msg in _fetch_inbox()}
    _last_away_state = away.is_away(config)
    call_task = asyncio.create_task(_watch_calls_loop(config))
    try:
        while True:
            current = away.is_away(config)
            if _last_away_state and not current:
                _last_debrief = _generate_brief(config)
                print("\n" + _last_debrief)
                try:
                    from ..memory import vault
                    vault.journal_append(config.vault_path, _last_debrief)
                except Exception:  # noqa: BLE001
                    pass
            _last_away_state = current
            if current:
                for msg in _fetch_inbox():
                    mid = _message_id(msg)
                    if mid not in _seen_ids and _eligible(msg):
                        await _handle_new_message(msg, config)
                    else:
                        _seen_ids.add(mid)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("away mode daemon stopped")
    finally:
        call_task.cancel()
        try:
            await call_task
        except asyncio.CancelledError:
            pass


def start(config) -> Optional[asyncio.Task]:
    global _task
    if _task and not _task.done():
        return _task
    _task = asyncio.create_task(run_pa_daemon(config))
    return _task


def stop(config=None) -> str:
    global _task
    if _task and not _task.done():
        _task.cancel()
    use_config = config or _active_config
    return latest_debrief(use_config) if use_config is not None else _last_debrief
