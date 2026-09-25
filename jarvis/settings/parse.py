"""From what was said to a settings change — English, Hindi and Hinglish, no model.

    "disable your animations"              → overlay.animations = off
    "animations wapas on kar do"           → overlay.animations = on
    "speak slightly faster"                → voice.speed + one step
    "thoda tez bolo"                       → voice.speed + one step
    "don't announce notifications for two hours" → notifications.level = quiet, for 2 h
    "keep listening for twelve seconds"    → voice.follow_up_s = 12
    "make the teaching pen less bright"    → teach.glow − one step
    "turn off away-mode replies immediately" → away.replies = off
    "undo the last preference change"      → undo

Deterministic on purpose: these are the most common requests there are, a model adds a round
trip and a way to be wrong, and a sentence this does not recognise is simply not a settings
request — it carries on to the rest of the command handlers.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from .registry import SETTINGS, Setting, value


@dataclass
class Request:
    op: str                          # set | adjust | undo | query | list
    setting: Optional[Setting] = None
    value: Any = None                # for set
    steps: float = 0.0               # for adjust: +1 = one step up
    until: Optional[float] = None    # a temporary change ends here
    said: str = ""

    def target(self) -> Any:
        """The value this request asks for, given what is in force now."""
        if self.op == "set":
            return self.value
        if self.op == "adjust" and self.setting is not None:
            s = self.setting
            return s.coerce(float(value(s.id)) + self.steps * float(s.step or 1))
        return None


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty five": 25, "thirty": 30, "forty five": 45, "sixty": 60, "an": 1, "a": 1, "half an": 0.5,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "das": 10,
    "baara": 12, "barah": 12, "bara": 12, "pandrah": 15, "bees": 20,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "दस": 10, "बारह": 12, "बीस": 20,
}
_NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True)) + r")"


def _number(token: str) -> Optional[float]:
    token = token.strip().lower()
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)
    return _NUMBER_WORDS.get(token)


_OFF = re.compile(
    r"(?i)\b(?:disable|disabled|turn(?:\s+\w+)?\s+off|switch(?:\s+\w+)?\s+off|off|stop|kill|close|hide|"
    r"no\s+more|don'?t|do\s+not|without|band|bandh|hatao|hata\s+do|mat|nahi\s+chahiye)\b|बंद|हटाओ|मत")
_ON = re.compile(
    r"(?i)\b(?:enable|enabled|turn(?:\s+\w+)?\s+on|switch(?:\s+\w+)?\s+on|on|start|resume|bring\s+back|"
    r"show|unhide|allow|chalu|shuru|wapas|waapas|vapas)\b|चालू|शुरू|वापस")
_UP = re.compile(
    r"(?i)\b(?:faster|quicker|speed\s+up|more|brighter|stronger|higher|longer|louder|increase|raise|"
    r"tez|tej|jaldi|zyada|jyada|badhao|badha\s+do)\b|तेज़|तेज|ज़्यादा|ज्यादा|बढ़ाओ")
_DOWN = re.compile(
    r"(?i)\b(?:slower|slow\s+down|less|dimmer|dim|softer|weaker|lower|shorter|decrease|reduce|"
    r"dheere|dheeme|dheema|kam|ghatao)\b|धीरे|कम|घटाओ")
_SLIGHT = re.compile(r"(?i)\b(?:slightly|a\s+(?:little|bit|touch)|little|bit|thoda|thodi|zara|jara)\b"
                     r"|थोड़ा|थोड़ी|ज़रा")
_MUCH = re.compile(r"(?i)\b(?:much|a\s+lot|way|bahut|kaafi|kafi)\b|बहुत")
_RESET = re.compile(r"(?i)\b(?:normal|default|usual|reset|pehle\s+jaisa)\b|सामान्य")
_UNDO = re.compile(
    r"(?i)^(?:please\s+)?(?:undo|revert|reverse)(?:\s+(?:that|it|this|the\s+last(?:\s+(?:preference|setting))?"
    r"(?:\s+change)?|my\s+last\s+(?:preference|setting)(?:\s+change)?|the\s+(?:preference|setting)\s+change))?"
    r"(?:\s+please)?$|^(?:put\s+it\s+back|change\s+it\s+back|wapas\s+(?:kar\s+do|pehle\s+jaisa\s+kar\s+do)|"
    r"pehle\s+jaisa\s+kar\s+do)$")
_DURATION = re.compile(
    r"(?i)\b(?:for|next|agle|for\s+the\s+next)\s+" + _NUM + r"\s*(hours?|hrs?|minutes?|mins?|ghante?|ghanta|minute)\b"
    r"|" + _NUM + r"\s*(ghante?|ghanta|minute)\s+(?:tak|ke\s+liye)")
_SECONDS = re.compile(r"(?i)" + _NUM + r"\s*(?:seconds?|secs?|second|sec|सेकंड)\b")
_QUERY = re.compile(r"(?i)^(?:what(?:'s| is| are)|how (?:fast|long|bright)|kitn[ai])\b")


def _clean(text: str) -> str:
    said = re.sub(r"(?i)^(?:hey\s+)?jarvis[,.!:\s]*", "", (text or "").strip())
    return said.rstrip(".!?। ").strip()


def _duration(said: str) -> Optional[float]:
    m = _DURATION.search(said)
    if not m:
        return None
    amount = _number(m.group(1) or m.group(3) or "")
    unit = (m.group(2) or m.group(4) or "").lower()
    if not amount:
        return None
    seconds = amount * (3600 if unit.startswith(("h", "g")) else 60)
    return min(seconds, 24 * 3600)


def _direction(said: str) -> Optional[bool]:
    """True for on, False for off, None when it says neither (or both without an order)."""
    off, on = _OFF.search(said), _ON.search(said)
    if off and not on:
        return False
    if on and not off:
        return True
    if on and off:
        # "turn it back on", "wapas on kar do", "don't turn it off": the later word decides,
        # except that a leading "don't"/"stop" negates the sentence.
        if re.match(r"(?i)^(?:don'?t|do not|stop)\b", said):
            return False
        return on.start() > off.start()
    return None


def _steps(said: str) -> float:
    up, down = _UP.search(said), _DOWN.search(said)
    if not (up or down) or (up and down):
        return 0.0
    size = 1.0 if _SLIGHT.search(said) else 3.0 if _MUCH.search(said) else 2.0
    return size if up else -size


# ------------------------------------------------------------------------ subjects
_SPEED_SUBJECT = re.compile(
    r"(?i)\b(?:speak|talk|talking|speaking|speech|voice|read|bolo|bol|boliye|bolna|bolne|baat)\b|बोल")
_SPEED_WORDS = re.compile(
    r"(?i)\b(?:faster|quicker|slower|speed|pace|rate|tez|tej|dheere|jaldi|gati)\b|तेज|धीरे|गति|रफ़्तार")
_ANIMATION = re.compile(r"(?i)\banimations?\b|एनिमेशन")
_REDUCED = re.compile(r"(?i)\b(?:reduced?\s+motion|less\s+motion|kam\s+motion)\b")
_OVERLAY = re.compile(r"(?i)\b(?:overlay|hud|pill)\b|ओवरले")
_BRIGHT = re.compile(r"(?i)\b(?:bright(?:ness|er)?|dim(?:mer)?|intensity|glow|faint(?:er)?|chamak)\b|चमक")
_PEN = re.compile(r"(?i)\b(?:teaching\s+(?:pen|overlay)|pen|drawing\s+glow|whiteboard\s+glow)\b|पेन")
_NOTIFY = re.compile(r"(?i)\b(?:notifications?|announcements?|announce|readouts?)\b|नोटिफिकेशन")
_NOTIFY_SPECIFIC = re.compile(r"(?i)\b(?:whatsapp|instagram|telegram|sms|gmail|email|calls?|from)\b")
_URGENT_ONLY = re.compile(r"(?i)\b(?:only\s+(?:urgent|important)|urgent\s+(?:ones\s+)?only|just\s+(?:urgent|important))\b")
_LISTEN = re.compile(r"(?i)\b(?:keep\s+listening|listen(?:ing)?\s+for|follow[\s-]?up|sunte\s+raho|sunna)\b|सुनते\s*रहो")
_AWAY_REPLIES = re.compile(r"(?i)\baway[\s-]*mode\s+repl(?:y|ies)|\baway\s+repl(?:y|ies)|\bauto[\s-]?repl(?:y|ies)")
_DICTATION = re.compile(r"(?i)\bdictation\s+(?:history|log)|\b(?:keep|save|store)\w*\s+(?:my\s+)?dictations?\b")
_VERBOSE = re.compile(
    r"(?i)\b(?:(?:shorter|briefer|brief|concise|longer|detailed|more\s+detailed|fuller)\s+(?:answers?|replies|responses)|"
    r"be\s+(?:brief|briefer|concise|more\s+detailed|detailed)|(?:talk|say)\s+less|chhota\s+jawab|short\s+mein|detail\s+mein|"
    r"normal\s+(?:length|answers?))\b")


def _speed(said: str) -> Optional[Request]:
    if not (_SPEED_SUBJECT.search(said) and _SPEED_WORDS.search(said)):
        return None
    s = SETTINGS["voice.speed"]
    if _RESET.search(said):
        return Request("set", s, s.default_value(), said=said)
    steps = _steps(said)
    if not steps and re.search(r"(?i)\b(?:tez|tej|jaldi)\b|तेज", said):
        steps = 1.0 if _SLIGHT.search(said) else 2.0
    if not steps and re.search(r"(?i)\bdheere\b|धीरे", said):
        steps = -1.0 if _SLIGHT.search(said) else -2.0
    if not steps:
        return None
    return Request("adjust", s, steps=steps, said=said)


def _bool(setting_id: str, said: str) -> Optional[Request]:
    direction = _direction(said)
    if direction is None:
        return None
    return Request("set", SETTINGS[setting_id], direction, said=said)


def _level(setting_id: str, said: str) -> Optional[Request]:
    s = SETTINGS[setting_id]
    if _RESET.search(said):
        return Request("set", s, s.default_value(), said=said)
    m = re.search(r"(?i)\b(?:to|at)\s+" + _NUM + r"\s*(?:%|percent)?", said)
    if m and s.kind == "float" and _number(m.group(1)) is not None:
        number = _number(m.group(1))
        return Request("set", s, number / 100 if number > 1 else number, said=said)
    steps = _steps(said)
    if steps:
        return Request("adjust", s, steps=steps / 2 if abs(steps) > 1 else steps, said=said)
    return None


def parse(text: str) -> Optional[Request]:
    said = _clean(text)
    if not said or len(said) > 160:
        return None
    if _UNDO.match(said):
        return Request("undo", said=said)
    if re.fullmatch(r"(?i)(?:what are|show|list)(?: me)? (?:your|my) (?:settings|preferences)", said):
        return Request("list", said=said)

    if _AWAY_REPLIES.search(said):
        return _bool("away.replies", said)
    if _DICTATION.search(said):
        return _bool("dictation.history", said)
    if _PEN.search(said) and (_BRIGHT.search(said) or _steps(said)):
        return _level("teach.glow", said)
    if _REDUCED.search(said):
        return _bool("motion.reduced", said)
    if _ANIMATION.search(said):
        return _bool("overlay.animations", said)
    if _OVERLAY.search(said):
        if _BRIGHT.search(said) or re.search(r"(?i)\b(?:dimmer|brighter|fainter)\b", said):
            return _level("overlay.intensity", said)
        if re.search(r"(?i)\b(?:hide|show|unhide|bring\s+back|turn\s+off|turn\s+on|off|on)\b", said):
            return _bool("overlay.visible", said)
        return None
    if _NOTIFY.search(said) and not _NOTIFY_SPECIFIC.search(said):
        s = SETTINGS["notifications.level"]
        until = _duration(said)
        if _URGENT_ONLY.search(said):
            return Request("set", s, "urgent", until=time.time() + until if until else None, said=said)
        direction = _direction(said)
        # Plain "notifications on/off" keeps its long-standing handler; this takes the timed
        # ("for two hours") and graded ("only urgent") forms.
        if direction is None or until is None:
            return None
        return Request("set", s, "all" if direction else "quiet",
                       until=time.time() + until if (until and not direction) else None, said=said)
    if _LISTEN.search(said):
        s = SETTINGS["voice.follow_up_s"]
        m = _SECONDS.search(said)
        if m and _number(m.group(1)) is not None:
            return Request("set", s, _number(m.group(1)), said=said)
        steps = _steps(said)
        return Request("adjust", s, steps=steps, said=said) if steps else None
    if _VERBOSE.search(said):
        s = SETTINGS["voice.verbosity"]
        if re.search(r"(?i)\b(?:shorter|briefer|brief|concise|less|chhota|short)\b", said):
            return Request("set", s, "brief", said=said)
        if re.search(r"(?i)\b(?:longer|detailed|fuller|detail)\b", said):
            return Request("set", s, "detailed", said=said)
        return Request("set", s, "normal", said=said)
    return _speed(said)
