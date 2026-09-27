"""One classifier for "what kind of request is this", before anything acts on it.

    question            "why do you use Kokoro?"               → answered normally
    command             "open YouTube"                          → existing handlers
    preference          "disable your animations"               → jarvis.settings (tier 0)
    diagnostic          "why is dictation slow?"                → look, report, change nothing
    small_repair        "WhatsApp search is showing the wrong contact again"
    small_capability    "add a command to read my battery aloud"
    large_change        "rewrite the voice pipeline in Rust"    → proposal only
    prohibited          "remove the approval step from messages" → refused
    external_side_effect "send Papa a message"                  → existing handlers, with approval

Rules first, because the common cases are obvious and a model is slower and can be talked
into things. ``semantic`` — a model — is consulted only when the rules find a request about
Jarvis's own behaviour that fits none of them, and even then it can only choose between the
labels above; it never widens what the source is allowed to do.

The *source* is checked separately (``jarvis.trust``): only the owner's own front-ends can turn
a report into a repair job. From anywhere else the classification is informational.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from ..trust import is_trusted, own_words
from . import policy

QUESTION = "question"
COMMAND = "command"
PREFERENCE = "preference"
DIAGNOSTIC = "diagnostic"
SMALL_REPAIR = "small_repair"
SMALL_CAPABILITY = "small_capability"
LARGE_CHANGE = "large_change"
PROHIBITED = "prohibited"
EXTERNAL = "external_side_effect"

LABELS = (QUESTION, COMMAND, PREFERENCE, DIAGNOSTIC, SMALL_REPAIR, SMALL_CAPABILITY,
          LARGE_CHANGE, PROHIBITED, EXTERNAL)
CODE_CHANGES = (SMALL_REPAIR, SMALL_CAPABILITY, LARGE_CHANGE)


@dataclass
class Classification:
    label: str
    component: Optional[policy.Component] = None
    reason: str = ""
    trusted: bool = False
    said: str = ""

    @property
    def may_start_repair(self) -> bool:
        return self.trusted and self.label in (SMALL_REPAIR, SMALL_CAPABILITY, DIAGNOSTIC)


# Weakening a safety control is refused whoever asks and however it is phrased.
_WEAKEN = re.compile(
    r"(?i)\b(?:remove|disable|drop|skip|bypass|turn\s+off|switch\s+off|get\s+rid\s+of|stop\s+(?:asking|requiring)|"
    r"ignore|override|lift|loosen|weaken|delete|without)\b.{0,50}?"
    r"\b(?:approvals?|approval\s+(?:step|requirements?|gate)|confirm(?:ation)?s?|permission(?:s| checks?)?|"
    r"authenticat\w*|authoriz\w*|passwords?|otp|two[\s-]factor|2fa|safety(?:\s+rules?)?|sandbox\w*|"
    r"restrictions?|guard(?:rail)?s?|host\s+checks?|origin\s+checks?|cors|disclosure|kill\s+switch|"
    r"self[\s-]?repair\s+(?:policy|rules?|limits?))\b")
_EXFIL = re.compile(
    r"(?i)\b(?:show|send|print|read|upload|post|email|share|give)\b.{0,30}\b(?:\.env|api\s+keys?|tokens?|"
    r"secrets?|credentials?|passwords?)\b")
_ESCALATE = re.compile(
    r"(?i)\b(?:give\s+yourself|grant\s+yourself|run\s+(?:yourself\s+)?as)\s+(?:root|sudo|admin|full\s+access)|"
    r"\bexpose\b.{0,30}\b(?:internet|public|0\.0\.0\.0|port)\b|\bmodify\s+your\s+(?:own\s+)?(?:code|rules)\s+to\s+"
    r"(?:disable|remove|bypass|ignore)")

_BUG = re.compile(
    r"(?i)\b(?:is|are|keeps?|kept)\s+(?:showing|picking|choosing|giving|opening|saying|reading|sending|playing)\s+"
    r"(?:the\s+)?wrong\b|\bwrong\s+(?:contact|person|number|chat|video|answer|result|word)s?\b|"
    r"\b(?:is|are|seems?|looks?)\s+(?:broken|buggy|not\s+working|stuck|glitch\w*)\b|"
    r"\b(?:doesn'?t|don'?t|didn'?t|won'?t|isn'?t|aren'?t|never)\s+(?:work|working|respond|load|start|open|hear|show)\b|"
    r"\b(?:keeps?|kept)\s+(?:failing|crashing|breaking|freezing|dropping|losing|cutting\s+off)\b|"
    r"\b(?:crash\w*|bug|regression|broke)\b|\bfix\s+(?:the|this|that|a)\s+\w+|\bfix\s+yourself\b|"
    r"\bgalat\b.{0,30}\b(?:dikha|bhej|chala|khol)|\bkaam\s+nahi\s+kar\s+raha\b|\btoot\s+gaya\b|\bखराब\b|\bगलत\b")
_DIAGNOSE = re.compile(
    r"(?i)^(?:why\s+(?:is|are|does|do|did|was)\b.{0,60}\b(?:slow|failing|fail|broken|wrong|lagging|crash\w*|"
    r"not\s+\w+ing)|what'?s\s+wrong\s+with|diagnose|investigate|look\s+into|check\s+(?:why|what'?s\s+wrong))")
_CAPABILITY = re.compile(
    r"(?i)\b(?:add|build|implement|teach\s+yourself|learn\s+to|make\s+(?:yourself|it)\s+able\s+to|"
    r"give\s+(?:yourself|me)\s+(?:a|an)\s+(?:way|option|command))\b.{0,60}\b(?:command|feature|option|"
    r"ability|shortcut|setting|mode|support)\b")
_LARGE = re.compile(
    r"(?i)\b(?:rewrite|re-?architect|redesign|refactor\s+(?:the\s+)?(?:whole|entire|all)|migrate\s+(?:to|from)|"
    r"port\s+(?:it|yourself|the\s+\w+)\s+to|replace\s+(?:the\s+)?(?:whole|entire)|new\s+architecture|"
    r"switch\s+(?:the\s+)?(?:framework|language|database))\b")
_EXTERNAL = re.compile(
    r"(?i)^(?:please\s+)?(?:send|message|text|whatsapp|email|mail|call|ring|post|tweet|book|buy|order|pay|"
    r"schedule|invite|reply\s+to|forward)\b")
# "message Papa that the TV is broken": a message with a body is an outside action, whatever it says.
_EXTERNAL_BODY = re.compile(r"(?i)^(?:please\s+)?(?:send|message|text|tell|email|mail)\b.{0,40}\b(?:that|saying|:)\s")
_ABOUT_SELF = re.compile(r"(?i)\b(?:you|your|yourself|jarvis)\b")
_QUESTION = re.compile(r"(?i)^(?:what|who|when|where|why|how|which|is|are|do|does|can|could|should|kya|kaise|kyun)\b")


def classify(text: str, source: str = "local", *,
             semantic: Optional[Callable[[str], str]] = None) -> Classification:
    """Label a request. Deterministic unless the rules leave a self-referential request unplaced
    and a ``semantic`` fallback is supplied."""
    said = own_words(text)
    said = re.sub(r"(?i)^(?:hey\s+)?jarvis[,.!:\s]*", "", said).strip().rstrip(".!?")
    trusted = is_trusted(source)
    component = policy.match_component(said)

    def label(name: str, reason: str) -> Classification:
        return Classification(name, component, reason, trusted, said)

    if not said:
        return label(QUESTION, "nothing said")
    if _WEAKEN.search(said) or _EXFIL.search(said) or _ESCALATE.search(said):
        return label(PROHIBITED, "asks to weaken a safety control or expose secrets")
    from ..settings.parse import parse as parse_setting

    if parse_setting(said) is not None:
        return label(PREFERENCE, "a known setting")
    # A report about one of Jarvis's own parts comes before the action verbs: "WhatsApp search
    # is showing the wrong contact" starts with a word that is also a verb.
    if _BUG.search(said) and (component is not None or _ABOUT_SELF.search(said)
                              or re.search(r"(?i)\bfix\s+yourself\b", said)) and not _EXTERNAL_BODY.search(said):
        return label(SMALL_REPAIR, "reports something of Jarvis's that misbehaves")
    if _EXTERNAL.match(said):
        return label(EXTERNAL, "an action with outside effects")
    if _LARGE.search(said):
        return label(LARGE_CHANGE, "an architectural change")
    if _DIAGNOSE.search(said):
        return label(DIAGNOSTIC, "asks why something misbehaves")
    if _CAPABILITY.search(said) and _ABOUT_SELF.search(said):
        return label(SMALL_CAPABILITY, "asks for a small new ability")
    if semantic is not None and _ABOUT_SELF.search(said) and re.search(
            r"(?i)\b(?:change|modify|update|improve|behave|stop|start)\b", said):
        try:
            guess = semantic(said)
        except Exception:  # noqa: BLE001
            guess = ""
        if guess in LABELS and guess != PROHIBITED:
            return label(guess, "interpreted")
    if _QUESTION.match(said):
        return label(QUESTION, "a question")
    return label(COMMAND, "everything else")
