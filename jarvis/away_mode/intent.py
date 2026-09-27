"""What the owner asked of away mode, from what they said.

    "I'm going out until 8. Handle my messages and calls."   → start, ends at the next 8 o'clock
    "Handle messages for two hours, only reply to family"    → start, 2h, family only
    "Handle WhatsApp but silence Instagram"                  → start, Instagram muted
    "Take messages, but don't hold conversations"            → start, take-message policy
    "Extend away mode by an hour" / "until 9"                → extend
    "Stop away mode" / "I'm back"                            → stop
    "What happened while I was away?"                        → summary

Only the owner's own speech reaches this parser — incoming messages never do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .session import tzinfo

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
              "thirty": 30, "forty": 40, "forty five": 45, "ninety": 90, "half an": 0.5, "half a": 0.5,
              "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "aadha": 0.5, "dedh": 1.5, "dhai": 2.5}

_START = re.compile(
    r"(?ix)\b(?:i'?m|i\s+am|i'?ll\s+be|i\s+will\s+be|main)\s+(?:going\s+out|heading\s+out|stepping\s+out|going|"
    r"away|out|unavailable|busy|leaving|bahar\s+ja\s+raha|bahar\s+jaa\s+raha|bahar\s+hoon|bahar\s+hu)"
    r"|\b(?:handle|cover|take\s+care\s+of|manage|answer|reply\s+to|deal\s+with|dekh\s+lena|sambhal\s+lena|sambhalna)\s+"
    r"(?:my\s+|all\s+my\s+|mere\s+)?(?:messages?|chats?|texts?|whatsapp|calls?|dms?)"
    r"|\btake\s+(?:my\s+)?messages\b|\baway\s+mode\s+(?:on|start)|\b(?:turn\s+on|start|enable|activate)\s+away\s+mode")
_MESSAGES_WORDS = re.compile(r"(?i)\b(?:messages?|chats?|texts?|whatsapp|calls?|reply|replies|handle|cover|dms?|away mode)\b")
_STOP = re.compile(
    r"(?ix)^(?:jarvis\s*,?\s*)?(?:stop\s+away\s+mode(?:\s+now)?|away\s+mode\s+off|turn\s+off\s+away\s+mode|"
    r"end\s+away\s+mode|disable\s+away\s+mode|i'?m\s+back|i\s+am\s+back|i'?m\s+home|main\s+aa\s+gaya|"
    r"stop\s+handling\s+my\s+messages|stop\s+replying(?:\s+to\s+(?:my\s+)?messages)?|i'?m\s+available(?:\s+now)?|"
    r"i\s+am\s+available(?:\s+now)?)[.!]*$")
_EXTEND = re.compile(r"(?i)\b(?:extend|prolong)\s+(?:the\s+)?away(?:\s+mode)?\b|\baway\s+mode\s+(?:till|until)\b|"
                     r"\b(?:i'?ll\s+be\s+back\s+later|make\s+it\s+(?:till|until)|keep\s+(?:it|away\s+mode)\s+on\s+(?:till|until|for))")
_SUMMARY = re.compile(
    r"(?ix)^(?:jarvis\s*,?\s*)?(?:what\s+happened\s+while\s+i\s+was\s+(?:away|out|gone)|what\s+did\s+i\s+miss|"
    r"(?:give\s+me\s+|read\s+(?:me\s+)?)?(?:my\s+|the\s+)?(?:away\s+)?(?:summary|debrief|briefing|catch[- ]?up)|"
    r"summari[sz]e\s+my\s+messages|any\s+(?:new\s+)?messages\s+while\s+i\s+was\s+(?:away|out))[?.!]*$")


@dataclass
class StartRequest:
    end: Optional[float]
    end_said: bool
    allowed_platforms: list[str] = field(default_factory=lambda: ["whatsapp"])
    muted_platforms: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)          # names/groups as said
    blocked: list[str] = field(default_factory=list)
    block_work_groups: bool = False
    reply_policy: str = "converse"
    interrupt: str = "urgent_only"
    calls: bool = False


_PLATFORMS = {"whatsapp": "whatsapp", "whats app": "whatsapp", "insta": "instagram", "instagram": "instagram",
              "telegram": "telegram", "sms": "sms", "texts": "sms", "text messages": "sms", "messenger": "messenger",
              "signal": "signal", "snapchat": "snapchat", "email": "email", "emails": "email", "gmail": "email"}
_PLAT_RE = "|".join(sorted((re.escape(k) for k in _PLATFORMS), key=len, reverse=True))


def _now_local(now: float, tz: str) -> datetime:
    return datetime.fromtimestamp(now, tzinfo(tz))


def parse_duration(text: str) -> Optional[float]:
    """Seconds, from 'for two hours', 'for 90 minutes', 'for an hour and a half', 'do ghante'."""
    t = text.lower()
    m = re.search(r"\b(?:for|next|agle|another|by)\s+(?:the\s+next\s+)?(?P<n>\d+(?:\.\d+)?|half an|half a|"
                  r"an|a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|"
                  r"forty five|forty|ninety|ek|do|teen|char|paanch|aadha|dedh|dhai)\s*"
                  r"(?P<u>hours?|hrs?|h|minutes?|mins?|m|ghante|ghanta|minute)\b(?P<half>\s+and\s+a\s+half)?", t)
    if not m:
        m = re.search(r"\b(?P<n>\d+(?:\.\d+)?|ek|do|teen|char|paanch|aadha|dedh|dhai)\s*(?P<u>ghante|ghanta|hours?|minute|minutes)\s*(?:ke\s+liye|tak)?\b(?P<half>)", t)
    if not m:
        return None
    n = m.group("n")
    value = float(n) if re.fullmatch(r"\d+(?:\.\d+)?", n) else float(_NUM_WORDS.get(n, 0))
    if m.group("half"):
        value += 0.5
    seconds = value * (60 if m.group("u").startswith("m") else 3600)
    return seconds if seconds > 0 else None


def parse_until(text: str, now: float, tz: str) -> Optional[float]:
    """Epoch seconds for 'until 8', 'till 8:30 pm', 'until 20:00', '8 baje tak', 'until midnight'."""
    t = text.lower()
    local = _now_local(now, tz)
    if re.search(r"\b(?:until|till|til)\s+midnight\b|\braat\s+12\s+baje\b", t):
        target = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return target.timestamp()
    if re.search(r"\b(?:until|till)\s+(?:the\s+)?(?:evening|tonight)\b", t):
        target = local.replace(hour=20, minute=0, second=0, microsecond=0)
        return (target if target > local else target + timedelta(days=1)).timestamp()
    m = re.search(r"\b(?:until|till|til|upto|up\s+to|by)\s+(?:about\s+|around\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*"
                  r"(?P<ap>a\.?m\.?|p\.?m\.?|o'?clock|in\s+the\s+(?:morning|evening|night)|tonight)?\b", t)
    if not m:
        m = re.search(r"\b(?:(?P<pre>shaam|raat|subah)\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*baje\s+tak\b(?P<ap>)", t)
    if not m:
        return None
    hour, minute = int(m.group("h")), int(m.group("m") or 0)
    if hour > 23 or minute > 59:
        return None
    ap = (m.group("ap") or "").replace(".", "")
    pre = m.groupdict().get("pre") or ""
    pm = ap.startswith("pm") or "evening" in ap or "night" in ap or "tonight" in ap or pre in {"shaam", "raat"}
    am = ap.startswith("am") or "morning" in ap or pre == "subah"
    candidates = []
    if hour >= 13 or hour == 0:
        candidates = [hour]
    elif pm:
        candidates = [hour % 12 + 12]
    elif am:
        candidates = [hour % 12]
    else:
        candidates = [hour % 12, hour % 12 + 12]
    best = None
    for h in candidates:
        target = local.replace(hour=h, minute=minute, second=0, microsecond=0)
        if target <= local:
            target += timedelta(days=1)
        if best is None or target < best:
            best = target
    return best.timestamp() if best else None


def _names(fragment: str) -> list[str]:
    parts = re.split(r"\s*(?:,|\band\b|\baur\b|&)\s*", fragment.strip())
    return [p.strip(" .") for p in parts if p.strip(" .") and len(p.strip()) <= 40]


def is_start(text: str) -> bool:
    t = text.strip()
    if _STOP.match(t) or _SUMMARY.match(t) or _EXTEND.search(t):
        return False
    return bool(_START.search(t)) and bool(_MESSAGES_WORDS.search(t) or re.search(r"(?i)\baway\b", t))


def parse_start(text: str, now: float, tz: str, default_hours: float = 2.0) -> StartRequest:
    t = " ".join(text.split())
    low = t.lower()
    end = parse_until(low, now, tz)
    if end is None:
        dur = parse_duration(low)
        end = now + dur if dur else None
    req = StartRequest(end=end if end else now + default_hours * 3600, end_said=end is not None)

    # platforms
    muted = set()
    for m in re.finditer(rf"\b(?:silence|mute|ignore|skip|not|don'?t\s+(?:handle|reply\s+(?:on|to)))\s+(?:my\s+)?({_PLAT_RE})\b", low):
        muted.add(_PLATFORMS[m.group(1)])
    handled = {_PLATFORMS[m.group(1)] for m in re.finditer(rf"\b({_PLAT_RE})\b", low)} - muted
    if handled:
        req.allowed_platforms = sorted(handled)
    req.muted_platforms = sorted(muted)
    req.calls = bool(re.search(r"(?i)\bcalls?\b", low))

    # who
    m = re.search(r"\bonly\s+(?:reply|respond|answer|talk)\s+to\s+(?P<who>[\w\s,&']+?)(?=$|[.;]|\s+(?:but|except|and\s+(?:silence|mute|take|only|don'?t))\b)", low) or \
        re.search(r"\b(?:reply|respond)\s+(?:only\s+)?to\s+(?P<who>family|my\s+family|vips?)\s+only\b", low) or \
        re.search(r"\bsirf\s+(?P<who>family|ghar\s+walon|gharwalon)\b", low)
    if m:
        req.allowed = _names(re.sub(r"^my\s+", "", m.group("who")))
    m = re.search(r"\b(?:everyone|everybody|all)\s+(?:except|but|other\s+than)\s+(?P<who>[\w\s,&']+?)(?=$|[.;]|\s+(?:and\s+(?:silence|mute|take|only)|but)\b)", low) or \
        re.search(r"\b(?:don'?t|do\s+not|never)\s+reply\s+to\s+(?P<who>[\w\s,&']+?)(?=$|[.;]|\s+but\b)", low)
    if m:
        who = m.group("who")
        if re.search(r"\bwork\s+groups?\b|\boffice\s+groups?\b", who):
            req.block_work_groups = True
            who = re.sub(r"\b(?:work|office)\s+groups?\b", "", who)
        req.blocked = [n for n in _names(who) if n not in {"groups", "group"}]

    # how much to say
    if re.search(r"\b(?:just|only)?\s*take\s+(?:a\s+)?messages?\b.*\b(?:don'?t|do\s+not|no|without)\s+(?:hold|have|start|keep)\s+"
                 r"(?:any\s+)?conversations?\b|\bjust\s+take\s+(?:a\s+)?messages?\b|\bonly\s+take\s+messages\b|"
                 r"\bdon'?t\s+(?:chat|hold\s+conversations?)\b", low):
        req.reply_policy = "take_message"
    if re.search(r"\b(?:don'?t|do\s+not)\s+reply\b(?!\s+to)|\bno\s+replies\b|\bjust\s+(?:log|record|note)\b", low):
        req.reply_policy = "silent"

    # when to interrupt
    if re.search(r"\bonly\s+(?:interrupt|disturb|bother|alert|ping)\s+me\s+(?:if|when)\s+(?:it'?s\s+)?(?:urgent|important|an\s+emergency)", low):
        req.interrupt = "urgent_only"
    elif re.search(r"\b(?:don'?t|do\s+not|never)\s+(?:interrupt|disturb|bother)\s+me\b", low):
        req.interrupt = "never"
    elif re.search(r"\b(?:tell|alert|ping)\s+me\s+(?:about\s+)?(?:every|each|all)\b", low):
        req.interrupt = "all"
    return req


def parse_extend(text: str, now: float, tz: str, current_end: Optional[float]) -> Optional[float]:
    """The new end time, or None when no time was said."""
    low = text.lower()
    until = parse_until(low, now, tz)
    if until:
        return until
    dur = parse_duration(low)
    if dur is None and re.search(r"\bby\s+(?:an?|one)\s+hour\b|\banother\s+hour\b|\bek\s+ghanta\b", low):
        dur = 3600.0
    if dur is None:
        return None
    return max(current_end or now, now) + dur


def is_stop(text: str) -> bool:
    return bool(_STOP.match(text.strip()))


def is_extend(text: str) -> bool:
    return bool(_EXTEND.search(text))


def is_summary(text: str) -> bool:
    return bool(_SUMMARY.match(text.strip()))
