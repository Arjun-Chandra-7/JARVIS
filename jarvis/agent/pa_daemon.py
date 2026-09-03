"""PA Guardian Daemon — real-time personal-assistant shield.

When Arjun marks himself unavailable (set_away / set_pa_status), this daemon:
  • Polls the WhatsApp bridge every 5 s for new messages → auto-replies via omnicore
  • Watches KDE Connect for incoming calls → logs + sends WhatsApp to caller
  • Keeps a full log of every interaction while Arjun is away
  • When Arjun comes back → prints/returns a concise brief of everything

Run via the `start_pa_daemon` tool (called from groq_tools).
The loop is a lightweight asyncio task; it doesn't block the main agent loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("jarvis.pa_daemon")

# Shared state
_task: Optional[asyncio.Task] = None
_seen_ts: set[int] = set()        # WhatsApp message timestamps already processed
_away_log: list[dict] = []        # everything that happened while away
_last_away_state: bool = False


def _wa_base() -> str:
    import os
    return f"http://127.0.0.1:{os.environ.get('WA_PORT', '8765')}"


def _fetch_inbox() -> list[dict]:
    try:
        import httpx
        return httpx.get(f"{_wa_base()}/inbox", timeout=4).json()
    except Exception:  # noqa: BLE001
        return []


async def _handle_new_message(msg: dict, config) -> None:
    from . import omnicore
    sender = msg.get("name") or msg.get("from", "Unknown")
    jid    = msg.get("from", sender)
    text   = msg.get("text", "")
    ts     = msg.get("ts", 0)
    _seen_ts.add(ts)

    entry = {
        "type": "whatsapp",
        "sender": sender,
        "jid": jid,
        "text": text,
        "ts": datetime.fromtimestamp(ts / 1000).strftime("%H:%M") if ts else "?",
        "pa_reply": "",
        "schedule": "",
    }
    try:
        result = await omnicore.handle_incoming_text(sender, jid, text, config=config)
        entry["pa_reply"] = result.get("pa_reply") or ""
        entry["schedule"] = result.get("schedule_detected") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"PA: omnicore error for {sender}: {exc}")
    _away_log.append(entry)
    logger.info(f"PA handled WhatsApp from {sender}: {text[:60]}")


async def _handle_call(call: dict, config) -> None:
    from . import omnicore
    caller = call.get("name") or call.get("number", "Unknown Caller")
    number = call.get("number", caller)
    entry = {
        "type": "call",
        "caller": caller,
        "number": number,
        "ts": datetime.now().strftime("%H:%M"),
        "pa_reply": "",
    }
    try:
        result = await omnicore.handle_incoming_call(caller, number, config=config)
        entry["pa_reply"] = result.get("pa_reply") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"PA: call handler error for {caller}: {exc}")
    _away_log.append(entry)
    logger.info(f"PA handled call from {caller}")


def _generate_brief(config=None) -> str:
    if not _away_log:
        return "All quiet while you were away — no messages or calls came in."
    lines = [f"PA Debrief — {len(_away_log)} event(s) while you were away:\n"]
    for i, e in enumerate(_away_log, 1):
        if e["type"] == "whatsapp":
            lines.append(f"  {i}. [{e['ts']}] WhatsApp from {e['sender']}: \"{e['text'][:100]}\"")
            if e.get("pa_reply"):
                lines.append(f"       Jarvis replied: \"{e['pa_reply'][:80]}\"")
            if e.get("schedule"):
                lines.append(f"       Schedule detected: {e['schedule']}")
        elif e["type"] == "call":
            lines.append(f"  {i}. [{e['ts']}] Call from {e['caller']} ({e['number']})")
            if e.get("pa_reply"):
                lines.append(f"       Jarvis messaged them: \"{e['pa_reply'][:80]}\"")
    return "\n".join(lines)


async def _watch_calls_loop(config) -> None:
    try:
        from ..integrations.phone.kdeconnect import KDEConnect
        kc = await KDEConnect(config.kde_device_id or None).connect()
        call_queue: asyncio.Queue = asyncio.Queue()
        kc.watch_calls(lambda c: call_queue.put_nowait(c))
        while True:
            try:
                call = await asyncio.wait_for(call_queue.get(), timeout=5.0)
                from . import away
                if away.is_away():
                    await _handle_call(call, config)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                return
    except Exception as exc:  # noqa: BLE001
        logger.info(f"PA: KDE Connect unavailable, call watching disabled ({exc})")


async def run_pa_daemon(config) -> None:
    global _last_away_state, _away_log, _seen_ts
    logger.info("PA Guardian daemon started.")
    _away_log = []
    _seen_ts = set()
    # Seed seen_ts so we don't reply to old messages
    for msg in _fetch_inbox():
        _seen_ts.add(msg.get("ts", 0))

    call_task = asyncio.create_task(_watch_calls_loop(config))
    try:
        while True:
            from . import away as away_mod
            currently_away = away_mod.is_away()

            # Returned from away — generate debrief
            if _last_away_state and not currently_away:
                brief = _generate_brief(config)
                print(f"\n{'='*60}\n{brief}\n{'='*60}\n")
                try:
                    from ..memory import vault as v
                    v.journal_append(config.vault_path, brief)
                except Exception:  # noqa: BLE001
                    pass
                _away_log.clear()
                _seen_ts = {msg.get("ts", 0) for msg in _fetch_inbox()}

            _last_away_state = currently_away

            if currently_away:
                for msg in _fetch_inbox():
                    if msg.get("ts", 0) not in _seen_ts:
                        await _handle_new_message(msg, config)

            await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("PA Guardian daemon stopped.")
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


def stop() -> str:
    global _task
    if _task and not _task.done():
        _task.cancel()
    return _generate_brief()
