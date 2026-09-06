"""Deterministic everyday commands, shared by text, desktop voice and mobile."""
from __future__ import annotations

import asyncio
import re


def clean_text(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.strip().startswith(("[", "(Reply in"))]
    return re.sub(r"^(?:hey\s+)?jarvis[,.!:\s]*", "", " ".join(lines).strip(), flags=re.I).strip()


async def handle(text: str, config, session_id: str = "local") -> str | None:
    raw = clean_text(text)
    command = raw.lower().rstrip(".!?")
    from .preferences import set_notifications
    if re.search(r"\bnotifications?\b", command) and re.search(
        r"\b(?:turn|switch|set|mute|unmute|disable|enable|stop|start|silence|resume|read|reading)\b", command
    ):
        turn_on = bool(re.search(r"\b(?:on|unmute|enable|enabled|resume|start reading|read them|read those|back on)\b", command))
        turn_off = bool(re.search(r"\b(?:off|mute|muted|disable|disabled|silence|silenced|stop|quiet|no more|don'?t read|do not read)\b", command))
        if turn_on and not turn_off:
            set_notifications(True)
            return "Notification readouts are on, sir."
        if turn_off and not turn_on:
            set_notifications(False)
            return "Notification readouts are off, sir. I'll still handle everything quietly."
        if turn_on and turn_off:  # e.g. "turn notifications back on" also contains no 'off'; guard anyway
            later_on = command.rfind("on") >= command.rfind("off")
            set_notifications(later_on)
            return "Notification readouts are on, sir." if later_on else "Notification readouts are off, sir."
    if re.fullmatch(r"(?:open|show|mirror)(?: my| the)? phone(?: screen)?(?: please)?", command):
        from .integrations.apps import phone_mirror
        ok, message = await asyncio.to_thread(phone_mirror)
        return "Opening your phone, sir." if ok else message
    if re.search(r"\b(?:i'?m|i am) (?:going out|heading out|away|stepping out|unavailable)\b", command) and re.search(r"\b(?:messages|message|reply|replies|handle|cover|deal with)\b", command):
        from .agent import away, pa_daemon
        away.set_away(raw, config)
        pa_daemon.start(config)
        return "Away mode is on, sir. I'll introduce myself as your assistant, handle new messages, and keep a record for your return."
    if re.fullmatch(r"(?:i'?m back|i am back|i'?m available|i am available|stop handling my messages|turn off away mode|away mode off)", command):
        from .agent import away
        away.set_available(config)
        return "Welcome back, sir. Away replies are off."
    if re.fullmatch(r"(?:join|open)(?: the| my)? (?:google )?meet(?: and (?:take )?notes)?", command):
        from .integrations import meet_bot
        result = await meet_bot.join_meet("https://meet.google.com/twa-pgjz-gss", "", config)
        return str(result)
    if re.fullmatch(r"(?:what did i miss|(?:read|show|give me)(?: me)?(?: my| the)? (?:away )?(?:messages? summary|message summary|debrief|catch[- ]?up))", command):
        from .agent import pa_daemon
        return pa_daemon.debrief(config)
    from .integrations import coding_jobs
    return await coding_jobs.handle_message(raw, session_id=session_id)
