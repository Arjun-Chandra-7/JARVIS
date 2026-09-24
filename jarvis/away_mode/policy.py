"""What an incoming message is about, how urgent it is, and whether a reply may say something.

Pure functions over text and a few facts about the sender; nothing here sends, stores or calls a
model. The message is data: an instruction inside it ("ignore your rules", "you are Aviral now")
is detected and reported as an attempt, never followed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import language

# ----------------------------------------------------------------------------- topics

_R = lambda pattern: re.compile(pattern, re.IGNORECASE)  # noqa: E731

RESTRICTED: dict[str, re.Pattern] = {
    "payment": _R(r"\b(?:pay|paid|payment|pay ?karo|paise|paisa|rupees?|rs\.?|inr|₹|upi|gpay|google pay|paytm|phonepe|"
                  r"bank|transfer|neft|imps|loan|udhaar|udhar|money|refund|invoice|bill|fees?|deposit)\b|₹|पैसे|पेमेंट"),
    "secret": _R(r"\b(?:otp|one[- ]time (?:password|code)|password|passcode|pass code|pin|cvv|verification code|"
                 r"security code|login code|2fa|auth(?:entication)? code|recovery code|seed phrase|private key|"
                 r"api key|token)\b|ओटीपी|पासवर्ड"),
    "private_info": _R(r"\b(?:aadhaar|aadhar|pan card|passport|his number|uska number|phone number of|address|"
                       r"id card|bank details|account number|salary|medical report)\b"),
    "media": _R(r"\b(?:send|bhej|bhejo|share|forward)\b.{0,30}\b(?:file|files|photo|photos|pic|pics|picture|"
                r"video|document|doc|pdf|screenshot|notes|attachment|ppt|zip)\b|\b(?:file|photo|pdf|notes)\b.{0,20}\b(?:bhej|bhejna|send)"),
    "legal": _R(r"\b(?:contract|agreement|legal|lawyer|court|notice|sign(?:ature)?|terms|nda|affidavit|stamp paper)\b"),
    "purchase": _R(r"\b(?:buy|purchase|order|kharid|kharido|kharidna|book(?:ing)? (?:the )?ticket|subscribe|checkout)\b"),
    "commitment": _R(r"\b(?:will he (?:come|attend|be there|join|pay|agree)|can he (?:come|attend|make it|join)|"
                     r"is he coming|confirm (?:that )?he|promise|aayega kya|ayega kya|aa (?:jayega|payega)|"
                     r"(?:aa|aana) sakta|attend karega|confirm kar(?:o|do|dena)? (?:ki|ke) woh|pakka aayega|"
                     r"will you come|are you coming)\b"),
    "sensitive": _R(r"\b(?:break ?up|breakup|divorce|angry|gussa|naraz|hate you|fight|jhagda|forgive|"
                    r"love you|miss you|relationship|cheat|disappointed|upset|hurt|crying|ro rahi|ro raha)\b"),
    "account_recovery": _R(r"\b(?:recover (?:my |his |the )?account|account recovery|reset (?:the |my |his )?password|"
                           r"locked out|verify (?:your|his) (?:account|identity)|security question)\b"),
    "location": _R(r"\b(?:where (?:is he|are you|is aviral|is arjun)|kaha(?:n)? (?:hai|ho|h)|location|live location|"
                   r"send (?:me )?(?:your|his) location|kis jagah)\b|कहाँ"),
    "contact_others": _R(r"\b(?:message|call|text|tell|inform|email|contact|add) (?:him|her|them|my|his|\w+) (?:for me|on my behalf)\b|"
                         r"\b(?:forward this to|add me to|call (?:my|his) (?:mom|dad|papa|mummy|boss))\b"),
    "policy_change": _R(r"\b(?:ignore (?:all |your |the |previous |prior |above )*(?:instructions|rules|policy|prompt)|"
                        r"disregard (?:your|the|all|previous)|forget (?:your|all|the) (?:rules|instructions)|"
                        r"system prompt|developer mode|jailbreak|you are now|act as|pretend (?:to be|you are)|"
                        r"new instructions|override|admin mode|reply as (?:him|aviral|arjun)|say you are|"
                        r"turn off away|disable away|change (?:your|the) (?:policy|settings|rules)|"
                        r"from now on you|tum ab se|rules bhool)\b"),
}

# Requests to relay something — "bol dena", "tell him", "ask him" — become a follow-up for the owner.
_RELAY = _R(r"\b(?:bol dena|bol de|bolna|bol diyo|bata dena|bata de|batana|keh dena|kehna|puch lena|puchna|"
            r"puch ke|puch kar|tell (?:him|aviral|arjun)|let (?:him|aviral|arjun) know|ask (?:him|aviral|arjun)|"
            r"pass (?:it|this|the message) on|remind (?:him|aviral|arjun))\b|बोल देना|बता देना|पूछ लेना")
_CALL_REQUEST = _R(r"\b(?:call (?:me|kar|karo|back|kr)|call karna|callback|call back|phone (?:kar|karo|uthao|utha)|"
                   r"ring me|call me|give me a call|call when|mujhe call|baat karni hai|baat karo)\b|कॉल कर|फोन कर")
_RETURN_Q = _R(r"\b(?:when (?:will|is) (?:he|aviral|arjun) (?:be )?(?:back|free|available|return)|when can i (?:call|talk|reach)|"
               r"kab (?:tak )?(?:aayega|ayega|aaoge|free|milega|available|wapas|aa raha)|kab free|is he (?:busy|available|free)|"
               r"free (?:hai|h) kya|busy (?:hai|h) kya|available (?:hai|h) kya|where is he)\b|कब")
_CLOSING = _R(r"^(?:ok(?:ay)?|k|kk|thanks?|thank you|thx|ty|cool|fine|theek hai|thik hai|achha|acha|haan theek|"
              r"rehne de|rehne do|chhod|chod|chodo|koi baat nahi|no problem|np|baad me baat kar(?:unga|enge)?|"
              r"baad me baat karunga|will talk later|talk later|later|bye|alright|got it|noted)\b")
_GREETING = _R(r"^(?:hi+|hello+|hey+|hii+|namaste|namaskar|yo|sup|oye|oi|bhai|bro|good (?:morning|evening|night|afternoon))\W*$")
_QUESTION = _R(r"\?|\b(?:kya|kab|kaise|kyun|kitne|kaun)\b|^(?:what|when|where|why|how|can|could|will|is|are|do|does|did|who)\b")

_EXPLICIT = _R(r"\b(?:urgent|urgently|emergency|asap|immediately|jaldi|turant|abhi ke abhi|important|zaroori|"
               r"zaruri|critical|right now)\b|जल्दी|तुरंत|अर्जेंट|ज़रूरी")
_DANGER = _R(r"\b(?:accident|hospital|ambulance|injured|injury|bleeding|blood|police|fire|died|death|passed away|"
             r"heart attack|unconscious|behosh|attack|emergency ward|icu|missing|lost child|tabiyat (?:bahut )?kharab|"
             r"not breathing|stroke|fracture|khoon|chot lagi|help me|bachao)\b|अस्पताल|एक्सीडेंट|पुलिस")
_TIME_CRITICAL = _R(r"\b(?:flight|train|bus|cab|pickup|pick up|gate|boarding|delayed|cancelled|canceled|rescheduled|"
                    r"reschedule|moved to|shifted to|shift ho|postponed|preponed|deadline|due (?:today|tonight|in)|"
                    r"exam|interview|submission|last date|in (?:\d+|ten|five|fifteen|thirty) (?:min|mins|minutes)|"
                    r"aaj raat|aaj hi|tonight|before \d)\b")
_SECURITY = _R(r"\b(?:suspicious (?:login|activity|sign[- ]?in)|new (?:login|sign[- ]?in|device)|account (?:locked|compromised|hacked)|"
               r"password (?:was )?changed|unauthori[sz]ed|hacked|fraud|scam alert|someone (?:logged|tried))\b")
_TIME_CHANGE = _R(r"\b(?:moved to|shifted to|rescheduled|postponed|preponed|changed to|instead of|ab \d+ baje|"
                  r"\d+ baje (?:ho gaya|kar diya|shift))\b")

# How other auto-responders talk. Two assistants answering each other forever is the failure.
_BOT = _R(r"\b(?:auto[- ]?reply|automatic reply|automated (?:message|reply|response)|this is an automated|"
          r"i(?:'m| am) (?:an? )?(?:ai|virtual|automated|digital) assistant|out of office|do not reply|"
          r"i am currently unavailable|is unavailable right now|will get back to you as soon as|"
          r"(?:'s|s) assistant\b|this number is not monitored|thank you for your message\. (?:we|i))\b")


@dataclass
class Classification:
    language: str
    normalised: str
    tags: list[str] = field(default_factory=list)          # relay, call_request, return_question, closing, ...
    restricted: list[str] = field(default_factory=list)
    injection: bool = False
    bot_like: bool = False
    urgency_score: int = 0
    urgency: str = "normal"                                # normal | important | urgent | emergency
    reasons: list[str] = field(default_factory=list)       # why that urgency — for the briefing

    @property
    def is_restricted(self) -> bool:
        return bool(self.restricted)


def classify(text: str, *, family: bool = False, vip: bool = False, known: bool = True,
             recent_incoming: int = 0, recent_calls: int = 0) -> Classification:
    """Topic, restriction and urgency for one message.

    ``recent_incoming``/``recent_calls``: how many messages/calls this sender sent in the last
    fifteen minutes, this one included. Urgency adds evidence up; a word alone is not enough.
    """
    raw = (text or "").strip()
    lang = language.detect(raw)
    norm = language.normalise(raw)
    probe = f"{raw.lower()} \n {norm}"
    c = Classification(language=lang, normalised=norm)

    for topic, pattern in RESTRICTED.items():
        if pattern.search(probe):
            c.restricted.append(topic)
    if "policy_change" in c.restricted:
        c.injection = True
    if _RELAY.search(probe):
        c.tags.append("relay")
    if _CALL_REQUEST.search(probe):
        c.tags.append("call_request")
    if _RETURN_Q.search(probe):
        c.tags.append("return_question")
    if _CLOSING.search(norm) and len(norm.split()) <= 7 and not _QUESTION.search(raw):
        c.tags.append("closing")
    if _GREETING.match(norm):
        c.tags.append("greeting")
    if _TIME_CHANGE.search(probe):
        c.tags.append("time_change")
    if _QUESTION.search(probe):
        c.tags.append("question")
    if _BOT.search(probe):
        c.bot_like = True

    score = 0
    if _EXPLICIT.search(probe):
        score += 1
        c.reasons.append("marked urgent")
    if "call_request" in c.tags:
        score += 1
        c.reasons.append("asked for a call")
    danger = bool(_DANGER.search(probe))
    if danger:
        score += 3
        c.tags.append("danger")
        c.reasons.append("possible emergency")
    if _TIME_CRITICAL.search(probe):
        score += 1
        c.tags.append("time_critical")
        c.reasons.append("time-sensitive")
    if _SECURITY.search(probe):
        score += 2
        c.tags.append("security")
        c.reasons.append("account security warning")
    if family or vip:
        score += 1
    if not known:
        score -= 1
    if recent_incoming >= 3:
        score += 2
        c.reasons.append(f"{recent_incoming} messages in a few minutes")
    if recent_calls >= 2:
        score += 2
        c.reasons.append(f"called {recent_calls} times")
    c.urgency_score = score
    if danger and (family or vip or recent_incoming >= 2 or recent_calls >= 2):
        c.urgency = "emergency"
    elif score >= 3:
        c.urgency = "urgent"
    elif score >= 2 or "time_change" in c.tags or "relay" in c.tags:
        c.urgency = "important"
    return c


# ----------------------------------------------------------------------------- reply checks

def _owner_voice(owner: str) -> re.Pattern:
    name = re.escape((owner or "").strip().lower()) or "(?!x)x"
    return _R(rf"\b(?:this is {name}|i am {name}|i'm {name}|im {name}|main {name} hoon|{name} here|"
              r"it's me|its me|yours truly|this is me)\b")
_COMMITS = _R(r"\b(?:he will (?:come|attend|pay|be there|send|buy|sign|call you at)|he'll (?:come|attend|pay|be there|send|buy|sign)|"
              r"i will (?:pay|send|transfer|buy|sign|share)|i'll (?:pay|send the|transfer|buy|sign|share)|"
              r"confirmed|it's a deal|deal done|agreed|guarantee|promise|woh aayega|wo aayega|pakka aayega|"
              r"aa jayega|pay kar dega|bhej dega|he agrees|he accepted)\b")
_LINK = _R(r"https?://|www\.|\b[\w.-]+\.(?:com|in|org|net|io|ly|me)\b")
_LONG_DIGITS = re.compile(r"\d[\d\s-]{5,}\d")


def validate_reply(reply: str, *, first_in_thread: bool, owner: str = "", disclosure_markers: tuple[str, ...] = ("jarvis",),
                   max_chars: int = 420) -> Optional[str]:
    """None if the reply may go out; otherwise why it may not."""
    text = (reply or "").strip()
    if not text:
        return "empty"
    if len(text) > max_chars:
        return "too long"
    lowered = text.lower()
    if first_in_thread and not any(m in lowered for m in disclosure_markers):
        return "missing disclosure"
    if _owner_voice(owner).search(text):
        return "speaks as the owner"
    if _COMMITS.search(text):
        return "makes a commitment"
    if _LINK.search(text):
        return "contains a link"
    if _LONG_DIGITS.search(text):
        return "contains a number"
    for topic in ("secret",):
        if RESTRICTED[topic].search(text) and not re.search(r"(?i)\b(?:can'?t|cannot|won'?t|never|nahi|mat)\b", text):
            return "mentions a secret"
    if re.search(r"(?i)<function|\[tool|\bassistant:|\bsystem:", text):
        return "protocol text"
    return None


# ----------------------------------------------------------------------------- redaction

_SECRETISH = re.compile(r"\b\d{4,8}\b")
_URL = re.compile(r"https?://\S+|www\.\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def redact(text: str, limit: int = 160) -> str:
    """A gist safe to keep and show: no codes, numbers, links or addresses; short."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = _URL.sub("[link]", t)
    t = _EMAIL.sub("[email]", t)
    t = _LONG_DIGITS.sub("[number]", t)
    if RESTRICTED["secret"].search(t):
        t = _SECRETISH.sub("[code]", t)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
