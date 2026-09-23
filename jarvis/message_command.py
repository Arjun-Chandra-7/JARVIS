"""'Message Papa on WhatsApp: I'll be home by eight' — parsed here, not by the model.

Left to the model, the recipient and the message came back tangled: "Papa on WhatsApp" as the
name, the platform folded into the text, or a message rewritten into something the owner never
said. The shape of the sentence already says which part is which, so it is read deterministically
and the message goes out exactly as spoken.

Also answers "send it" and "cancel" while a message is waiting for approval.
"""
from __future__ import annotations

import asyncio
import re

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

# A bare "ok" is filler as often as it is consent, so it does not send on its own.
_CONFIRM = re.compile(r"(?i)^(?:(?:yes|yeah|yep|haan|han|ok(?:ay)?|sure)[,\s]+)?"
                      r"(?:send it|send|bhej(?:\s+do|\s+de|o)?|do it|go ahead|confirm(?:ed)?)(?:\s+please)?$"
                      r"|^(?:yes|yeah|yep|haan|han)(?:\s+please)?$")
_CANCEL = re.compile(r"(?i)^(?:no|nah|nahi|nhi|cancel(?: it)?|don'?t send(?: it)?|do not send(?: it)?|"
                     r"mat bhejo|rehne do|rehne de|nhi rehne de|nahi rehne do|stop)(?:\s+please)?$")


def parse(text: str) -> tuple[str, str] | None:
    """(recipient as said, message verbatim), or None when this is not a message request."""
    said = (text or "").strip()
    for pattern in (_ENGLISH, _HINGLISH):
        m = pattern.match(said)
        if not m:
            continue
        who = m.group("who").strip().strip("\"'")
        msg = m.group("msg").strip()
        if len(msg) >= 2 and msg[0] == msg[-1] and msg[0] in "\"'“”":
            msg = msg[1:-1].strip()
        if who.lower().split()[0] in _NOT_A_PERSON or not msg or len(who.split()) > 5:
            return None
        return who, msg
    return None


async def handle(text: str, config) -> str | None:
    from .integrations import whatsapp

    said = (text or "").strip().rstrip(".!")
    if whatsapp.pending_send():
        if said and _CANCEL.fullmatch(said):
            return whatsapp.cancel_pending()["message"]
        if said and _CONFIRM.fullmatch(said):
            return (await asyncio.to_thread(whatsapp.confirm_pending))["message"]

    parsed = parse(text)
    if not parsed:
        return None
    who, msg = parsed
    return (await asyncio.to_thread(whatsapp.smart_send, who, msg))["message"]
