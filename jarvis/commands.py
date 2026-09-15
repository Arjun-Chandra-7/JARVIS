"""Deterministic everyday commands, shared by text, desktop voice and mobile."""
from __future__ import annotations

import asyncio
import re


def clean_text(text: str) -> str:
    """Strip the machinery around what the user actually said.

    Context the front-ends attach arrives on its own line: screen descriptions and incoming
    messages in [brackets], instructions to the model in (parentheses). Matching only the exact
    prefix "(Reply in" meant that rewording the voice instruction leaked the whole of it into the
    command — "open netflix" became "netflix (Spoken request. DO the action with a tool first...)"
    and matched nothing. Any bracketed or parenthesised line is machinery, not speech.
    """
    lines = [line for line in text.splitlines() if not line.strip().startswith(("[", "("))]
    return re.sub(r"^(?:hey\s+)?jarvis[,.!:\s]*", "", " ".join(lines).strip(), flags=re.I).strip()


_SLEEP_RE = re.compile(
    r"^(?:go(?: to)? sleep|goodnight|good night|stand down|power down|power off|"
    r"shut down|shutdown|that'?ll be all|that'?s all|dismissed|sleep mode|take a break|"
    r"go(?: to)? sleep now|sleep now)$")
_WAKE_RE = re.compile(
    r"^(?:wake up|wake|come back|i need you|you (?:there|awake)|are you (?:there|awake)|"
    r"resume|back online|power (?:on|up)|boot up|reactivate)$")


async def handle(text: str, config, session_id: str = "local") -> str | None:
    from .hinglish import normalise

    # Hindi and Hinglish are rewritten into English once, here, so every handler below works in
    # both without knowing it. A sentence that is already English, or Hindi this does not
    # recognise, comes back unchanged and carries on to the model as it was said.
    raw = normalise(clean_text(text))
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
    if re.fullmatch(r"(?:scan|list|show|check)(?: the| my| nearby| all)? bluetooth(?: devices?)?"
                    r"|bluetooth(?: devices?| scan)|what(?:'?s| is) (?:on |around )?bluetooth", command):
        from .presence import bluetooth
        return await asyncio.to_thread(bluetooth.report, config)
    # Volume, brightness and mute: one meaning, one number, no ambiguity. Offered the registered
    # set_brightness tool at the top of its shortlist, the local 3B brain answered "I don't have a
    # tool for that" and then claimed the brightness had been set.
    from .system_command import handle as system_something
    adjusted = await system_something(raw, config)
    if adjusted is not None:
        return adjusted

    # An explicit visible click is a computer action, not an "open a website" request. Route it
    # through the active native UI/screen targeter before the general open-command parser.
    from .screen_command import handle as screen_something
    clicked = await screen_something(raw, config)
    if clicked is not None:
        return clicked

    # Requests that are several actions in a row — "play the latest X video on YouTube" — are
    # planned and then executed step by step, each one checked, rather than handed to the model
    # as one instruction it has to remember its way through. Before open_command, which would
    # see only the first action.
    from .task_runner import handle as run_task
    carried_out = await run_task(raw, config)
    if carried_out is not None:
        return carried_out

    # Last, so the specific handlers above keep priority: "open phone" and "open meet" are theirs.
    # Everything else shaped like "open X" has one meaning, and measured, routing it through the
    # local 3B model called no tool at all on three of five attempts at "open friends".
    from .open_command import handle as open_something
    opened = await open_something(raw, config)
    if opened is not None:
        return opened

    from .integrations import coding_jobs
    return await coding_jobs.handle_message(raw, session_id=session_id)
