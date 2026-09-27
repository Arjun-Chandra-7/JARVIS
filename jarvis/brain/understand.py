"""What a request is, worked out without asking a model.

Asking a model which model to use costs a round trip before any work starts. Everything here is
patterns and counts, so it runs in well under a millisecond and its decisions can be read back.

The important distinction is conversation versus action. "How would I message Papa?" is a
question about messaging and must never send anything; "Message Papa saying I'll be late" is an
action and goes to the contact and approval pipeline. The existing detector
(``agent.action_claims.asks_for_an_action``) is one signal; the overrides below are the cases it
gets wrong in the daily-assistant sense (a how-to question, a plan, a draft, a memory request).
"""
from __future__ import annotations

import re

from . import privacy, study
from .request import BrainRequest, Cap, Intent, Source

# --- language -----------------------------------------------------------------------------------
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HINGLISH_WORDS = re.compile(
    r"(?i)\b(?:kya|kyun|kyu|kaise|kaisa|kaisi|hai|hain|hota|hoti|hote|mein|mai|mujhe|mera|meri|"
    r"samjha|samjhao|samjhaiye|batao|bata|bolo|karo|kar|nahi|nahin|aur|ka|ki|ke|ko|se|yeh|ye|woh|"
    r"wo|thoda|zyada|accha|acha|theek|matlab|kyunki|phir|abhi|kal|bhai|yaar|haan|na)\b")


def language(text: str) -> str:
    t = text or ""
    if _DEVANAGARI.search(t):
        latin = len(re.findall(r"[A-Za-z]{3,}", t))
        deva = len(re.findall(r"[ऀ-ॿ]+", t))
        return "hi" if deva >= latin else "hinglish"
    hits = len(_HINGLISH_WORDS.findall(t))
    words = max(1, len(re.findall(r"[A-Za-z']+", t)))
    # Two Hindi function words, or a fifth of the sentence, is Hinglish. "Ka", "ki" and "se"
    # alone are not enough — English has none of them, but names and abbreviations do.
    if hits >= 2 and hits / words >= 0.15:
        return "hinglish"
    return "en"


# --- freshness ----------------------------------------------------------------------------------
# "current" alone is not freshness: "current ka direction" is electricity.
_FRESH = re.compile(
    r"(?i)\b(?:latest|newest|recent(?:ly)?|today'?s?|tonight|this (?:week|month|year)|right now|"
    r"news|headlines?|breaking|live score|score (?:of|in)|who won|results? (?:of|for) the|"
    r"current (?:price|rate|status|version|president|prime minister|pm|ceo|champion|weather|affairs|events)|"
    r"price of|stock|share price|exchange rate|weather|forecast|release date|"
    r"is .{1,40} still|as of (?:now|today)|20[2-9]\d)\b")


def is_fresh(text: str) -> bool:
    return bool(_FRESH.search(text or ""))


# --- vision -------------------------------------------------------------------------------------
_VISION = re.compile(
    r"(?i)\b(?:on (?:my|the) screen|this (?:image|picture|photo|screenshot|diagram|graph|chart)|"
    r"in (?:the|this) (?:image|picture|photo|screenshot)|camera|what am i looking at|"
    r"what(?:'s| is) (?:this|that) (?:on|in)|currently on (?:my )?screen|look at (?:this|my screen))\b")


def wants_vision(text: str, images: list | None = None) -> bool:
    return bool(images) or bool(_VISION.search(text or ""))


# --- intent overrides ---------------------------------------------------------------------------
_HOW_TO = re.compile(r"(?i)^\s*(?:how (?:would|do|can|could|should) (?:i|we|you|one)|how to|what(?:'s| is) the way to|"
                     r"is it possible to|can you explain how|kaise)\b")
_MEMORY = re.compile(r"(?i)\b(?:remember (?:this|that|to)|don'?t forget|note (?:this|that) down|"
                     r"what was i (?:doing|working on)|what did i (?:say|ask) (?:about|earlier))\b")
_PLAN = re.compile(r"(?i)\b(?:make|create|draw up|give me) (?:a |me a )?(?:plan|schedule|routine|timetable)\b|\bplan (?:my|for my|the) \w+")
_CALENDAR = re.compile(r"(?i)\b(?:calendar|meeting|reminder|remind me|event)\b")
_WRITE = re.compile(r"(?i)\b(?:help me (?:write|draft|reply|phrase|word)|draft (?:a|an|the|me)|write (?:a|an|me a) "
                    r"(?:message|email|mail|reply|note|letter|caption|post)|rephrase|reword|how should i (?:say|reply))\b")
_CODE = re.compile(r"(?i)(```|\b(?:python|javascript|typescript|rust|golang|java|c\+\+|sql|regex|stack ?trace|"
                   r"traceback|segfault|compile error|function|refactor|debug (?:this|my)|bug in)\b)")
# "Find the latest on X" gathers information; "open YouTube and search X" acts in an app.
_INFO_ONLY = re.compile(r"(?i)^\s*(?:please\s+)?(?:find(?: out)?|look up|search(?: for)?|check|get me|tell me|what|who|when|where|how)\b")
_IN_AN_APP = re.compile(r"(?i)\b(?:open|play|on youtube|in (?:the )?browser|on google|tab)\b")
_CONVERSATIONAL = re.compile(
    r"(?i)^\s*(?:(?:hey\s+)?jarvis[,\s]+)?(?:please\s+)?(?:tell me|explain|describe|define|compare|summari[sz]e|"
    r"what|why|how|who|when|where|which|is|are|does|do|can you (?:explain|tell)|"
    r"give me (?:a|an|some|the) (?:fact|example|idea|definition|summary|overview|list|analogy))\b")
_SEND_VERBS = re.compile(r"(?i)^\s*(?:please\s+)?(?:message|text|whatsapp|send|email|mail|call|ping|reply to|tell(?! me\b)(?! us\b))\b")

# --- output style -------------------------------------------------------------------------------
_STYLE = [
    ("final_only", re.compile(r"(?i)\b(?:only (?:give|the) (?:the )?final (?:exam )?answer|just the (?:final )?answer|"
                              r"just answer|sirf answer|only answer)\b")),
    ("steps", re.compile(r"(?i)\b(?:only steps|step[- ]by[- ]step|stepwise|steps only)\b")),
    ("short", re.compile(r"(?i)\b(?:short answer|briefly|in short|one line|tl;?dr|quick answer|chhota)\b")),
    ("detailed", re.compile(r"(?i)\b(?:in detail|detailed|elaborate|explain (?:fully|thoroughly)|deep dive)\b")),
    ("simple", re.compile(r"(?i)\b(?:simply|simple|like i'?m (?:five|5|a beginner)|easy words|aasan)\b")),
]

# --- difficulty ---------------------------------------------------------------------------------
_HARD = re.compile(r"(?i)\b(?:prove|proof|derive|optimi[sz]e|trade-?offs?|how many (?:ways|combinations)|"
                   r"probability|puzzle|riddle|constraints?|step by step|think carefully|carefully|edge cases?|"
                   r"architecture|design a|complexity|diagnos\w+|root cause|multi-?step|compare and contrast|"
                   r"synthesi[sz]e|if and only if|all of the following)\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


def difficulty(text: str) -> float:
    """0..1 from what can be counted: hard-task markers, constraints, numbers, length."""
    t = text or ""
    words = len(t.split())
    score = 0.0
    score += min(0.45, 0.15 * len(_HARD.findall(t)))
    score += min(0.2, 0.03 * len(_NUMBER.findall(t)))
    score += min(0.2, words / 600)
    score += min(0.15, 0.05 * t.count("\n"))
    if re.search(r"(?i)\b(?:if|then|unless|each|every|exactly|at least|at most)\b.*\b(?:if|then|unless|each|every|exactly|at least|at most)\b", t):
        score += 0.1
    return round(min(1.0, score), 2)


def output_style(text: str) -> str:
    for name, pattern in _STYLE:
        if pattern.search(text or ""):
            return name
    return "concise"


def understand(req: BrainRequest, *, asks_for_an_action=None) -> BrainRequest:
    """Fill in language, privacy, intent, capabilities, style and difficulty on the request."""
    if asks_for_an_action is None:
        from ..agent.action_claims import asks_for_an_action
    text = req.text or ""
    req.language = language(text)
    screen = bool(re.search(r"(?i)screen|screenshot", text))
    sens = privacy.classify(text, req.source, bool(req.images), screen)
    req.privacy, req.privacy_reasons = sens.level, sens.reasons
    req.output_style = output_style(text)
    req.difficulty = difficulty(text)
    req.fresh = is_fresh(text)
    caps = {Cap.CHAT}
    if req.language in {"hi", "hinglish"}:
        caps |= {Cap.MULTILINGUAL, Cap.HINDI if req.language == "hi" else Cap.HINGLISH}

    st = study.detect(text)
    how_to = bool(_HOW_TO.search(text))
    if wants_vision(text, req.images):
        req.intent = Intent.VISION
        caps.add(Cap.VISION)
    elif st is not None:
        req.intent = Intent.STUDY
        req.study = st.to_dict()
        if st.mode in {"formal", "check"}:
            caps.add(Cap.HIGH_ACCURACY)
        if st.style:
            req.output_style = st.style
    elif _MEMORY.search(text):
        req.intent = Intent.MEMORY
        caps.add(Cap.TOOLS)
        req.tool_permission = "act"
        req.side_effect_risk = "low"
    elif how_to:
        req.intent = Intent.CONVERSATION
    elif _WRITE.search(text) and not _SEND_VERBS.search(text):
        req.intent = Intent.WRITING
    elif _PLAN.search(text) and not _CALENDAR.search(text):
        req.intent = Intent.PLANNING
    elif req.fresh and (not asks_for_an_action(text) or (_INFO_ONLY.search(text) and not _IN_AN_APP.search(text))):
        req.intent = Intent.RESEARCH
        caps.add(Cap.RESEARCH)
    elif _CONVERSATIONAL.search(text) and not _IN_AN_APP.search(text) and not _SEND_VERBS.search(text):
        req.intent = Intent.CODE if _CODE.search(text) else Intent.CONVERSATION
        if req.intent == Intent.CODE:
            caps.add(Cap.CODE)
    elif asks_for_an_action(text) or _SEND_VERBS.search(text):
        req.intent = Intent.ACTION
        caps.add(Cap.TOOLS)
        req.tool_permission = "act"
        req.side_effect_risk = "high" if _SEND_VERBS.search(text) else "low"
    elif _CODE.search(text):
        req.intent = Intent.CODE
        caps.add(Cap.CODE)
    else:
        req.intent = Intent.CONVERSATION

    if req.difficulty >= 0.6 or req.intent == Intent.CODE and req.difficulty >= 0.4:
        caps |= {Cap.REASONING, Cap.HIGH_ACCURACY}
    if len(text) > 12000:
        caps.add(Cap.LONG_CONTEXT)
    if req.offline_required:
        caps.add(Cap.LOCAL)
    # Untrusted sources may supply content but never ask for tools on the owner's behalf.
    if req.source not in Source.TRUSTED:
        caps.discard(Cap.TOOLS)
        req.tool_permission = "none"
        if req.intent in {Intent.ACTION, Intent.MEMORY}:
            req.intent = Intent.CONVERSATION
    req.capabilities = caps
    req.output_tokens = {"short": 200, "final_only": 250, "concise": 500, "simple": 500, "steps": 700,
                         "detailed": 1200, "exam": 700, "quiz": 250}.get(req.output_style, 600)
    return req
