"""'Message Papa on WhatsApp: I'll be home by eight' — parsed here, not by the model.

Left to the model, the recipient and the message came back tangled: "Papa on WhatsApp" as the
name, the platform folded into the text, or a message rewritten into something the owner never
said. The shape of the sentence already says which part is which, so it is read deterministically
and the message goes out exactly as spoken.

A sentence is taken apart into what each piece is for:

    "ok now  message  98xxxxxxxx  with the country code plus 91 .  Bye."
     filler  verb     recipient   country-code instruction        closing (not the message)

"ok now message <number> with the country code plus 911. Bye." used to match nothing — no
separator, no message text — and went to the model, which answered "Yes." and did nothing. A
request with a recipient and no text is still a request: Jarvis asks what to send and remembers
who it was for, so the next thing said is the message.

Approving a held message ("send it", "haan bhej do") is the shared approval manager's job —
see approvals.py.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Optional

from . import phone_numbers

_PLATFORM = r"(?:whats\s?app|wa)"
_NOT_A_PERSON = {"me", "us", "him", "her", "them", "it", "everyone", "everybody", "someone",
                 "somebody", "anyone", "jarvis", "you", "this", "that"}

_ENGLISH = re.compile(
    rf"""(?ix)^(?:please\s+)?
    (?:send\s+(?:a\s+)?(?:{_PLATFORM}\s+)?(?:message|msg|text)\s+to|message|msg|text|{_PLATFORM}|
       (?:send\s+)?{_PLATFORM}\s+to|tell|ping)
    \s+(?P<who>.+?)
    (?:\s+(?:on|via|over|through)\s+{_PLATFORM})?
    (?:\s*[:,\-–]\s*|\s+(?:that|saying|to\s+say)\s+)
    (?P<msg>\S.*)$""")

# "papa ko whatsapp karo ki …", "papa ko bol do late aaunga", "papa ko message bhejo: …"
_HINGLISH = re.compile(
    rf"""(?ix)^(?P<who>.+?)\s+ko\s+
    (?:(?:{_PLATFORM}|message|msg|text)\s+(?:kar|bhej)(?:\s+dena|\s+do|\s+de|dena|o)?|
       (?:bol|keh|bata)(?:\s+dena|\s+do|\s+de|dena|o)?)
    (?:\s+(?:ki|ke)\b)?\s*[:,]?\s*(?P<msg>\S.*)$""")

# A request that names who but not what: "message 98…", "send a WhatsApp to Papa", "text Rohit".
# "tell" and "ping" are left out: "tell Rohit" with nothing after it is not a message request.
_NO_BODY = re.compile(
    rf"""(?ix)^(?:please\s+)?
    (?:send\s+(?:a\s+)?(?:{_PLATFORM}\s+)?(?:message|msg|text)\s+to|message|msg|text|
       (?:send\s+)?{_PLATFORM}(?:\s+to)?)
    \s+(?P<who>.+?)(?:\s+(?:on|via|over|through)\s+{_PLATFORM})?\s*$""")

# "Send 'Bye' to this number on WhatsApp" — the text first, then who. Unquoted text is only taken
# when the sentence says WhatsApp or names a number, so "send the file to Papa" is not a message.
_TEXT_FIRST = re.compile(
    rf"""(?ix)^(?:please\s+)?send\s+(?P<msg>["'“‘].+?["'”’]|.+?)\s+to\s+(?P<who>.+?)
    (?P<platform>\s+(?:on|via|over|through)\s+{_PLATFORM})?\s*$""")

# Talk around the request that is not part of it.
_LEADING_FILLER = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|alright|all\s+right|so|now|right|and|then|hmm|umm?|please|"
    r"jarvis|hey\s+jarvis|also|next|acha|achha|chalo)[,.!\s]+)+")

# What a reply of only these words means is "nothing yet", not the message text.
_NOT_A_BODY = re.compile(
    r"(?i)^(?:yes|yeah|yep|ok(?:ay)?|haan|han|ji|hmm|bye|bye bye|goodbye|ok bye|thanks?|thank you|"
    r"that'?s all|bas|done|no|nahi|please)[.!?]*$")

_THIS_NUMBER = re.compile(r"(?i)^(?:this|that|the\s+same|the)\s+(?:number|no\.?|contact|person)$")
_QUOTED = re.compile(r"^[\"'“‘](?P<t>.*)[\"'”’]$", re.S)


@dataclass
class Request:
    """One message request, taken apart."""
    who: str = ""              # the recipient as said, or the E.164 number
    body: str = ""             # verbatim message text; "" when none was given
    number: str = ""           # E.164 when the recipient is a number
    problem: str = ""          # a question to ask instead of acting
    closing: bool = False      # the person also said goodbye

    @property
    def label(self) -> str:
        return phone_numbers.mask(self.number) if self.number else self.who


@dataclass
class _Draft:
    who: str
    number: str
    expires: float


_DRAFT_TTL_S = 120.0
_DRAFTS: dict[str, _Draft] = {}


def _unquote(text: str) -> tuple[str, bool]:
    text = (text or "").strip()
    m = _QUOTED.match(text)
    return (m.group("t").strip(), True) if m and len(text) >= 2 else (text, False)


def _strip_filler(text: str) -> str:
    return _LEADING_FILLER.sub("", (text or "").strip()).strip()


def _recipient(raw_who: str, full: str) -> tuple[str, str, str]:
    """(who, e164, problem) from the recipient part of the sentence."""
    who = (raw_who or "").strip().strip("\"'").strip(" ,.")
    who = re.sub(rf"(?i)\s+(?:on|via|over|through)\s+{_PLATFORM}$", "", who).strip()
    m, parsed = phone_numbers.find(who)
    if m is None and phone_numbers.mentions_indian_code(who):
        # "message 98… with the country code plus 91": the number may sit before the phrase.
        m, parsed = phone_numbers.find(full)
    if m is not None:
        if parsed.ok:
            return parsed.e164, parsed.e164, ""
        tail = re.sub(r"\D", "", m.group(0))[-4:]
        return "", "", (f"I heard a number ending {tail}, but it isn't a valid mobile number. "
                        "Could you say all ten digits again?")
    who = phone_numbers.COUNTRY_CODE_PHRASE.sub("", who).strip(" ,.")
    return who, "", ""


def read(text: str) -> Optional[Request]:
    """A message request, taken apart — or None when this is not one."""
    from .audio.conversation import split_closing

    said, closing = split_closing(phone_numbers.spoken_country_code((text or "").strip()))
    said = _strip_filler(said)
    if not said:
        return None

    m = _TEXT_FIRST.match(said)
    if m and not re.match(r"(?i)^(?:a\s+)?(?:whats\s?app\s+)?(?:message|msg|text)$", m.group("msg").strip()):
        body, quoted = _unquote(m.group("msg"))
        target = m.group("who").strip()
        numeric = phone_numbers.find(target)[0] is not None or _THIS_NUMBER.match(target)
        if body and (quoted or m.group("platform") or numeric):
            return _finish(target, body, said, closing, explicit=True)

    for pattern in (_ENGLISH, _HINGLISH):
        m = pattern.match(said)
        if m:
            body, quoted = _unquote(m.group("msg"))
            who = m.group("who")
            if not quoted and _NOT_A_BODY.match(body):
                body = ""          # "Message Rohit, yes." — nothing to send yet
            got = _finish(who, body, said, closing, explicit=quoted)
            if got is not None:
                return got

    m = _NO_BODY.match(said)
    if m:
        return _finish(m.group("who"), "", said, closing, explicit=False)
    return None


def _finish(raw_who: str, body: str, said: str, closing: bool, *, explicit: bool) -> Optional[Request]:
    who_said = (raw_who or "").strip()
    if _THIS_NUMBER.match(who_said.strip(" ,.")):
        return Request(who="this number", body=body, closing=closing)
    who, number, problem = _recipient(who_said, said)
    if problem:
        return Request(problem=problem, closing=closing)
    if not who:
        return None
    if not number:
        first = who.lower().split()[0] if who.split() else ""
        if first in _NOT_A_PERSON or len(who.split()) > 5:
            return None
    return Request(who=who, body=body, number=number, closing=closing)


def parse(text: str) -> tuple[str, str] | None:
    """(recipient as said, message verbatim), or None when there is no message text."""
    req = read(text)
    if req is None or req.problem or not req.body:
        return None
    return req.who, req.body


# --------------------------------------------------------------------------- drafts

def pending_draft(session: str) -> Optional[_Draft]:
    draft = _DRAFTS.get(session)
    if draft and draft.expires < time.time():
        _DRAFTS.pop(session, None)
        return None
    return draft


def clear_drafts() -> None:
    _DRAFTS.clear()


def _answer_to_draft(text: str, session: str) -> Optional[Request]:
    """The message text, when the last reply asked for it."""
    draft = pending_draft(session)
    if draft is None:
        return None
    from .audio.conversation import is_session_end

    said, _closing = (text or "").strip(), False
    if is_session_end(said) or re.match(r"(?i)^(?:cancel|never ?mind|don'?t|rehne d[oe]|chhodo|mat bhejo)\b", said):
        _DRAFTS.pop(session, None)
        return Request(problem="Okay, I won't send anything.")
    said = _strip_filler(said)
    said = re.sub(r"(?i)^(?:(?:just\s+)?(?:say|send|write|tell\s+(?:him|her|them))|likh\s+do|bolo)\s+", "", said)
    said = re.sub(rf"(?i)\s+(?:on|via)\s+{_PLATFORM}$", "", said)
    body, quoted = _unquote(said)
    if not body or (not quoted and _NOT_A_BODY.match(body)):
        return None
    _DRAFTS.pop(session, None)
    return Request(who=draft.number or draft.who, number=draft.number, body=body)


async def handle(text: str, config) -> str | None:
    from . import context, route_log
    from .integrations import whatsapp

    session = context.current()
    req = read(text)
    from_draft = False
    if req is None:
        req = _answer_to_draft(text, session)
        if req is None:
            return None
        from_draft = not req.problem
    elif req.who == "this number":
        draft = pending_draft(session)
        if draft is None:
            return "Which number? Say it with all ten digits."
        _DRAFTS.pop(session, None)
        req.who, req.number = draft.number or draft.who, draft.number
        from_draft = True

    if req.problem:
        route_log.record(intent="message.compose", action="clarify", platform="whatsapp")
        return req.problem
    if not req.body:
        _DRAFTS[session] = _Draft(who=req.who, number=req.number, expires=time.time() + _DRAFT_TTL_S)
        route_log.record(intent="message.compose", action="ask_body", platform="whatsapp",
                         recipient=req.label, closing=req.closing)
        if req.number:
            return f"Okay, {req.label} on WhatsApp. What should I send to this number?"
        return f"What should I send to {req.who}?"

    route_log.record(intent="message.compose", action="send_or_propose", platform="whatsapp",
                     recipient=req.label, chars=len(req.body), closing=req.closing)
    # Text gathered over two turns is always shown before it goes: the second turn might not
    # have been meant as the message at all.
    result = await asyncio.to_thread(lambda: whatsapp.smart_send(req.who, req.body, confirm=from_draft))
    route_log.record(intent="message.compose", action=str(result.get("status", "")), platform="whatsapp",
                     recipient=req.label)
    return result["message"]


def looks_like_message(text: str) -> bool:
    """Cheap check for the voice loop: does this sentence ask to message someone?"""
    try:
        return read(text) is not None
    except Exception:  # noqa: BLE001
        return False
