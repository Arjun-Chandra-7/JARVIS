"""Deterministic everyday commands, shared by text, desktop voice and mobile."""
from __future__ import annotations

import asyncio
import re


def clean_text(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.strip().startswith(("[", "(Reply in"))]
    return re.sub(r"^(?:hey\s+)?jarvis[,.!:\s]*", "", " ".join(lines).strip(), flags=re.I).strip()


_SLEEP_RE = re.compile(
    r"^(?:go(?: to)? sleep|goodnight|good night|stand down|power down|power off|"
    r"shut down|shutdown|that'?ll be all|that'?s all|dismissed|sleep mode|take a break|"
    r"go(?: to)? sleep now|sleep now)$")
_WAKE_RE = re.compile(
    r"^(?:wake up|wake|come back|i need you|you (?:there|awake)|are you (?:there|awake)|"
    r"resume|back online|power (?:on|up)|boot up|reactivate)$")


async def handle(text: str, config, session_id: str = "local") -> str | None:
    raw = clean_text(text)
    command = raw.lower().rstrip(".!?")

    from .power import asleep, set_asleep
    if _WAKE_RE.match(command):
        was = asleep()
        set_asleep(False)
        return "I'm back online, sir." if was else "I'm already here, sir."
    if _SLEEP_RE.match(command):
        set_asleep(True)
        return "Going to sleep, sir. Say “Jarvis, wake up” when you need me."
    # While asleep, ignore everything except the wake phrase above (voice also enforces this).
    if asleep():
        return "I'm asleep, sir. Say “Jarvis, wake up” to bring me back."

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
    if re.fullmatch(r"(?:who(?:'?s| is)(?: around| here| nearby| in the room| with me)|"
                    r"is (?:anyone|anybody|someone) (?:here|around|nearby|with me)|"
                    r"(?:scan|check)(?: the)? room|human radar|radar)", command):
        from .presence import service as presence
        return await asyncio.to_thread(presence.summary, config)
    if re.fullmatch(r"(?:join|open)(?: the| my)? (?:google )?meet(?: and (?:take )?notes)?", command):
        from .integrations import meet_bot
        result = await meet_bot.join_meet("https://meet.google.com/twa-pgjz-gss", "", config)
        return str(result)
    if re.fullmatch(r"(?:what did i miss|(?:read|show|give me)(?: me)?(?: my| the)? (?:away )?(?:messages? summary|message summary|debrief|catch[- ]?up))", command):
        from .agent import pa_daemon
        return pa_daemon.debrief(config)
    if re.fullmatch(r"(?:scan|list|show|check)(?: the| my| nearby| all)? wi[- ]?fi(?: networks?| passwords?)?"
                    r"|wi[- ]?fi(?: networks?| passwords?)|what wi[- ]?fi(?:s| networks?)?(?: are)?(?: around| near(?:by| me))?", command):
        from .integrations import wifi_scan
        return await asyncio.to_thread(wifi_scan.report)
    from .integrations import coding_jobs
    return await coding_jobs.handle_message(raw, session_id=session_id)
