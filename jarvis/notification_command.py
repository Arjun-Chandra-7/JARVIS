"""Spoken control over what gets announced.

    "Don't announce Instagram for two hours"      mute an app, for a while or until unmuted
    "Stop reading messages from this group"       mute the conversation just announced
    "Stop reading messages from Rohit"            mute a sender
    "Only interrupt me for family"                everyone else goes to the summary
    "Summarise my notifications"                  what was held back, grouped
    "Read that again"                             the last announcement

The rules live on disk (notifications.py), so this can run in the web process while the voice
process does the announcing.
"""
from __future__ import annotations

import re

from . import notifications as n

_APPS = r"(?P<app>whats\s?app|instagram|insta|telegram|messenger|signal|sms|text messages)"
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "half an": 0.5}
_FOR = r"(?:\s+for\s+(?P<n>\d+|a|an|one|two|three|four|five|six|ten|twelve|fifteen|twenty|thirty|half an)\s+" \
       r"(?P<unit>minutes?|mins?|hours?|hrs?|days?))?"

_MUTE_APP = re.compile(rf"(?i)^(?:don'?t|do not|stop|no more|mute|silence)\s+(?:announc\w*|read\w*|" \
                       rf"notify\w*|telling me about)?\s*(?:my\s+)?{_APPS}(?:\s+(?:messages|notifications))?{_FOR}$")
_MUTE_APP_2 = re.compile(rf"(?i)^(?:mute|silence)\s+(?:my\s+)?{_APPS}(?:\s+(?:messages|notifications))?{_FOR}$")
_UNMUTE_APP = re.compile(rf"(?i)^(?:unmute|resume|start announcing|announce)\s+(?:my\s+)?{_APPS}(?:\s+again)?"
                         rf"(?:\s+(?:messages|notifications))?(?:\s+again)?$")
_MUTE_FROM = re.compile(rf"(?i)^(?:stop|don'?t|do not)\s+(?:reading|announcing|telling me about)\s+"
                        rf"(?:messages?|notifications?)?\s*from\s+(?P<who>.+?){_FOR}$")
_ONLY_FAMILY = re.compile(r"(?i)^only\s+(?:interrupt|disturb|bother|tell)\s+me\s+(?:for|about)\s+(?:my\s+)?family$")
_EVERYONE = re.compile(r"(?i)^(?:interrupt|tell)\s+me\s+(?:for|about)\s+(?:everyone|everything|all messages)"
                       r"(?:\s+again)?$|^(?:turn off|stop)\s+family[- ]only(?: mode)?$")
_SUMMARY = re.compile(r"(?i)^(?:summari[sz]e|summary of|what(?:'s| are)? (?:in )?)\s*(?:my\s+)?"
                      r"(?:notifications?|messages i missed)$|^what did i miss$")
_AGAIN = re.compile(r"(?i)^(?:read|say|repeat)\s+(?:that|the last)\s*(?:message|notification)?\s*(?:again)?$")


def _seconds(match) -> float | None:
    if not match.group("n"):
        return None
    raw = match.group("n").lower()
    amount = float(raw) if raw.isdigit() else _NUM.get(raw, 1)
    unit = match.group("unit").lower()
    scale = 60 if unit.startswith("m") else 86400 if unit.startswith("d") else 3600
    return amount * scale


def _app(raw: str) -> str:
    raw = raw.lower().replace(" ", "")
    return {"insta": "instagram", "textmessages": "sms"}.get(raw, raw)


def _span(seconds: float | None) -> str:
    if seconds is None:
        return "until you say otherwise"
    if seconds < 3600:
        return f"for {int(seconds // 60)} minutes"
    hours = seconds / 3600
    return f"for {int(hours)} hour{'s' if hours != 1 else ''}" if hours < 24 else f"for {int(hours // 24)} days"


def handle(text: str) -> str | None:
    said = (text or "").strip().rstrip(".!?")
    if not said:
        return None

    if _SUMMARY.match(said):
        return n.shared_summary()
    if _AGAIN.match(said):
        last = n.last_announcement().get("last_said")
        return last or "There's nothing to read again."
    if _ONLY_FAMILY.match(said):
        n.set_only_family(True)
        return "Only family will interrupt you. Everyone else waits for the summary."
    if _EVERYONE.match(said):
        n.set_only_family(False)
        return "Back to announcing everyone."

    m = _UNMUTE_APP.match(said)
    if m:
        app = _app(m.group("app"))
        n.unmute("app", app)
        return f"Announcing {app.title()} again."
    m = _MUTE_APP.match(said) or _MUTE_APP_2.match(said)
    if m:
        app, secs = _app(m.group("app")), _seconds(m)
        n.mute("app", app, secs)
        return f"I won't announce {app.title()} {_span(secs)}. It'll be in your summary."
    m = _MUTE_FROM.match(said)
    if m:
        who, secs = m.group("who").strip(), _seconds(m)
        if re.fullmatch(r"(?i)(?:this|that|the)\s+(?:group|chat|conversation|thread)|them|him|her", who):
            last = n.last_announcement()
            if last.get("last_thread"):
                n.mute("thread", last["last_thread"], secs)
                return f"I'll stop reading that conversation {_span(secs)}."
            if last.get("last_who"):
                n.mute("sender", last["last_who"], secs)
                return f"I'll stop reading messages from {last['last_who']} {_span(secs)}."
            return "I haven't announced anything yet, so I don't know which conversation you mean."
        name = n.speakable_sender(re.sub(r"(?i)\s+(?:group|chat)$", "", who))
        n.mute("sender", name, secs)
        return f"I'll stop reading messages from {name} {_span(secs)}."
    return None
