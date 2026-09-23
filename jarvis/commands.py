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


# The deterministic layer, in the order it is tried. Each entry answers with a string when the
# request is its own and None when it is not, so the first one to recognise the request wins.
#
# This is a list rather than a run of if-statements because more than one thing walks it now:
# a plain turn goes through it once, and a chained request — "generate a picture of X, then find
# a whiteboard and draw it" — walks it once per part. Two copies of this order would drift, and
# the order is the whole design.
#
# Imported inside each entry rather than at module scope: most turns touch two or three of these,
# and importing the browser stack, the OCR stack and the diffusion stack on every turn would cost
# far more than the routing saves.
def deterministic_handlers():
    """(name, handler) pairs, most specific first. Handlers return None to pass."""

    async def chain(text, config):
        # First, because a chained request contains the others' requests inside it. "Generate a
        # picture of Iron Man, then draw it on a whiteboard" would otherwise be taken by the
        # whiteboard handler, which would look for a picture that had not been made yet.
        from .chain_command import handle as run_chain
        return await run_chain(text, config)

    async def modes(text, config):
        # "Study mode" and "Iron Man mode" change what the whole machine is for, so they are
        # recognised before anything that might read "close everything" as a request to close
        # one thing.
        from .mode_command import handle as f
        return await f(text, config)

    async def system(text, config):
        # Volume, brightness and mute: one meaning, one number, no ambiguity. Offered the
        # registered set_brightness tool at the top of its shortlist, the local 3B brain answered
        # "I don't have a tool for that" and then claimed the brightness had been set.
        from .system_command import handle as f
        return await f(text, config)

    async def screen_click(text, config):
        # An explicit visible click is a computer action, not an "open a website" request.
        from .screen_command import handle as f
        return await f(text, config)

    async def take_browser_control(text, config):
        # "Restart Opera with control" — the sentence Jarvis suggests whenever a browser action
        # fails. It was reachable only through the model's tool choice, so it almost never happened.
        from .browser_control_command import handle as f
        return await f(text, config)

    async def linkedin(text, config):
        # Before the site finder and the generic opener, both of which would take "open my
        # LinkedIn" at face value and send you to linkedin.com — technically what was asked for
        # and never what was wanted.
        from .linkedin_command import handle as f
        return await f(text, config)

    async def find_a_site(text, config):
        # "Find a site that does X" — opened and checked, not chosen from a blurb. Before the
        # plain draw handler, which would otherwise take the "draw…" half of a combined request.
        from .find_site import handle as f
        return await f(text, config)

    async def read_screen(text, config):
        # "What does my screen say" — read it. Left to the model this took twenty-three seconds
        # and came back with "It's a screenshot of your current desktop."
        from .screen_read_command import handle as f
        return await f(text, config)

    async def describe_project(text, config):
        # "Tell me about this project" — the files are read here, deterministically, and only the
        # describing is left to the model. Asked to do both it answered "I'll check your files
        # now" and did nothing.
        from .project_command import handle as f
        return await f(text, config)

    async def make_a_picture(text, config):
        # "Make me a picture of X" — generated here and saved. Before the draw handler, which is
        # the other half of the same idea: draw traces a reference onto an open whiteboard, this
        # makes a file. Their verbs do not overlap.
        from .imagine_command import handle as f
        return await f(text, config)

    async def draw_something(text, config):
        # "Draw me X" — find a reference picture, reduce it to lines, draw them on the canvas.
        from .draw_command import handle as f
        return await f(text, config)

    async def self_improve(text, config):
        # "Fix yourself" — look at what has failed repeatedly and try to mend it.
        from .selfimprove.command import handle as f
        return await f(text, config)

    async def coding_agent(text, config):
        # "Ok, but now we need to add X" at the editor. Before the task runner, which would try
        # to plan it as browser steps, and before open_command, which would see "open a terminal".
        # It only takes the turn when an agent is already in conversation, one was named, or the
        # editor is the window in front of you.
        from .coding_command import handle as f
        return await f(text, config)

    async def run_task(text, config):
        # Requests that are several actions in a row — "play the latest X video on YouTube" — are
        # planned and executed step by step, each one checked, rather than handed to the model as
        # one instruction it has to remember its way through. Before open_command, which would
        # see only the first action.
        from .task_runner import handle as f
        return await f(text, config)

    async def open_something(text, config):
        # Last, so the specific handlers above keep priority: "open phone" and "open meet" are
        # theirs. Everything else shaped like "open X" has one meaning, and measured, routing it
        # through the local 3B model called no tool at all on three of five attempts at
        # "open friends".
        from .open_command import handle as f
        return await f(text, config)

    return [
        ("chain", chain),
        ("modes", modes),
        ("system", system),
        ("screen_click", screen_click),
        ("browser_control", take_browser_control),
        ("linkedin", linkedin),
        ("find_site", find_a_site),
        ("read_screen", read_screen),
        ("project", describe_project),
        ("imagine", make_a_picture),
        ("draw", draw_something),
        ("self_improve", self_improve),
        ("coding", coding_agent),
        ("task", run_task),
        ("open", open_something),
    ]


async def handle(text: str, config, session_id: str = "local") -> str | None:
    from .hinglish import normalise

    from . import context

    # Hindi and Hinglish are rewritten into English once, here, so every handler below works in
    # both without knowing it. A sentence that is already English, or Hindi this does not
    # recognise, comes back unchanged and carries on to the model as it was said.
    #
    # Then the follow-up is filled in from the last turn — "play the second one" becomes "play
    # <that title>" — so the handlers below, which keep no state of their own, still see a whole
    # request. Both rewrites leave anything they do not recognise exactly as it was.
    context.set_current(session_id)
    # Known mishearings of command words are put right first, because everything below is trying
    # to match words: "wide-board" and "vibe both" are both "whiteboard", and no amount of
    # careful parsing downstream recovers a word that never arrived.
    from .misheard import fix as fix_misheard
    raw = context.resolve(normalise(fix_misheard(clean_text(text))), session_id)
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
    # "is my mic working" — a question that could only be answered by trying to talk and failing.
    # Matched on meaning rather than a fixed phrase, because there is no one way people ask it.
    from .audio import mic_report

    if mic_report.asked(command):
        return await asyncio.to_thread(mic_report.check, config.audio_input_device,
                                       command)
    if re.fullmatch(r"(?:scan|list|show|check)(?: the| my| nearby| all)? wi[- ]?fi(?: networks?| passwords?)?"
                    r"|wi[- ]?fi(?: networks?| passwords?)|what wi[- ]?fi(?:s| networks?)?(?: are)?(?: around| near(?:by| me))?", command):
        from .integrations import wifi_scan
        return await asyncio.to_thread(wifi_scan.report)
    if re.fullmatch(r"(?:scan|list|show|check)(?: the| my| nearby| all)? bluetooth(?: devices?)?"
                    r"|bluetooth(?: devices?| scan)|what(?:'?s| is) (?:on |around )?bluetooth", command):
        from .presence import bluetooth
        return await asyncio.to_thread(bluetooth.report, config)
    for _name, _handler in deterministic_handlers():
        answer = await _handler(raw, config)
        if answer is not None:
            return answer

    from .integrations import coding_jobs
    return await coding_jobs.handle_message(raw, session_id=session_id)
