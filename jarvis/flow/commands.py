"""Edits said aloud while dictating — "new paragraph", "delete last sentence", "make this formal".

A command is recognised only when it is the whole utterance (with "please", "jarvis" or "ok"
allowed around it). "I need to delete the last sentence of my essay" is dictation, and so is
anything the grammar below does not match exactly: a phrase that might be either stays text,
because an edit nobody asked for is worse than three extra words.

Every match carries a confidence. Exact phrases are 1.0; looser phrasings ("can you make this
sound more formal") are lower; below THRESHOLD nothing is treated as a command.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

THRESHOLD = 0.75

_POLITE = r"(?:(?:please|jarvis|ok(?:ay)?|hey\s+jarvis|now|and)[,\s]+)*"
_TAIL = r"(?:[,\s]+(?:please|jarvis|karo|kar\s+do|do))*[.!?।]*"


@dataclass
class Command:
    kind: str
    args: dict = field(default_factory=dict)
    confidence: float = 1.0


def _rx(body: str) -> re.Pattern:
    return re.compile(rf"(?i)^{_POLITE}(?:{body}){_TAIL}$")


# (kind, pattern, confidence, args)
_GRAMMAR: list[tuple[str, re.Pattern, float, dict]] = [
    ("new_paragraph", _rx(r"new\s+paragraph|naya\s+paragraph|नया\s+पैराग्राफ"), 1.0, {}),
    ("new_line", _rx(r"new\s+line|next\s+line|nayi\s+line|नई\s+लाइन"), 1.0, {}),
    ("bullet_list", _rx(r"(?:make\s+(?:this|that|it)\s+(?:a\s+)?)?bullet(?:ed)?\s+(?:list|points)|bullets"), 1.0, {}),
    ("numbered_list", _rx(r"(?:make\s+(?:this|that|it)\s+(?:a\s+)?)?numbered\s+list"), 1.0, {}),
    ("delete_last_word", _rx(r"(?:delete|remove)\s+(?:the\s+)?last\s+word|last\s+word\s+hatao"), 1.0, {}),
    ("delete_last_sentence", _rx(r"(?:delete|remove)\s+(?:the\s+)?last\s+sentence|"
                                 r"(?:last|pichla)\s+sentence\s+hatao|ye\s+hatao|isko\s+hatao|"
                                 r"scratch\s+that|delete\s+that|ये\s+हटाओ"), 1.0, {}),
    ("undo", _rx(r"undo(?:\s+that|\s+it)?|undo\s+karo"), 1.0, {}),
    ("select_last_sentence", _rx(r"select\s+(?:the\s+)?last\s+sentence"), 1.0, {}),
    ("transform", _rx(r"make\s+(?:this|that|it)\s+(?:more\s+)?formal|formal\s+(?:banao|kar\s+do)|"
                      r"isko\s+formal\s+banao"), 1.0, {"style": "formal"}),
    ("transform", _rx(r"make\s+(?:this|that|it)\s+shorter|shorten\s+(?:this|that|it)|chhota\s+karo|"
                      r"isko\s+chhota\s+karo"), 1.0, {"style": "shorter"}),
    ("transform", _rx(r"fix\s+(?:the\s+)?grammar|correct\s+(?:the\s+)?grammar|grammar\s+theek\s+karo"),
     1.0, {"style": "grammar"}),
    ("transform", _rx(r"translate\s+(?:this|that|it)\s+(?:to|into)\s+english|english\s+mein\s+(?:karo|likho|translate\s+karo)"),
     1.0, {"style": "english"}),
    ("transform", _rx(r"write\s+(?:this|that|it)\s+in\s+hinglish|hinglish\s+mein\s+likho|make\s+(?:this|it)\s+hinglish"),
     1.0, {"style": "hinglish"}),
    ("cancel", _rx(r"cancel(?:\s+(?:that|this|dictation))?|rehne\s+do|रहने\s+दो"), 1.0, {}),
    ("copy_last", _rx(r"copy\s+(?:the\s+)?last\s+(?:transcript|dictation)"), 1.0, {}),
    ("paste_last", _rx(r"paste\s+(?:the\s+)?last\s+(?:transcript|dictation)"), 1.0, {}),
    ("retry", _rx(r"retry(?:\s+(?:that|insertion|the\s+last\s+one))?|try\s+(?:that\s+)?again"), 0.9, {}),
    ("forget_correction", _rx(r"forget\s+(?:that|the\s+last)\s+correction"), 1.0, {}),
    ("clear_history", _rx(r"clear\s+(?:my\s+|the\s+)?dictation\s+history|dictation\s+history\s+(?:saaf|clear)\s+karo"), 1.0, {}),
    ("discard_last", _rx(r"discard\s+(?:the\s+)?last\s+(?:transcript|dictation)"), 1.0, {}),
    ("add_to_dictionary", _rx(r"add\s+(?:this|that)(?:\s+(?:name|word))?\s+to\s+(?:my\s+)?dictionary"), 1.0, {}),
]

# Looser shapes, lower confidence: accepted, but only just.
_LOOSE: list[tuple[str, re.Pattern, float, dict]] = [
    ("transform", _rx(r"(?:can|could)\s+you\s+make\s+(?:this|that|it)\s+(?:sound\s+)?(?:more\s+)?formal"), 0.85,
     {"style": "formal"}),
    ("transform", _rx(r"(?:can|could)\s+you\s+(?:make\s+(?:this|that|it)\s+shorter|shorten\s+(?:this|that|it))"), 0.85,
     {"style": "shorter"}),
    ("transform", _rx(r"(?:can|could)\s+you\s+fix\s+(?:the\s+)?grammar"), 0.85, {"style": "grammar"}),
]

_REPLACE = re.compile(rf"(?i)^{_POLITE}(?:replace|change)\s+[\"']?(.+?)[\"']?\s+(?:with|to)\s+[\"']?(.+?)[\"']?{_TAIL}$")
_ALWAYS = re.compile(rf"(?i)^{_POLITE}always\s+(?:spell|write)\s+(?:this|that|it)(?:\s+name)?\s+as\s+[\"']?(.+?)[\"']?{_TAIL}$")


def parse(text: str) -> Optional[Command]:
    said = re.sub(r"\s+", " ", (text or "").strip())
    if not said or len(said.split()) > 12:
        return None
    for kind, pattern, confidence, args in _GRAMMAR + _LOOSE:
        if pattern.match(said) and confidence >= THRESHOLD:
            return Command(kind, dict(args), confidence)
    m = _REPLACE.match(said)
    if m and len(m.group(1).split()) <= 5 and len(m.group(2).split()) <= 6:
        return Command("replace", {"old": m.group(1).strip(), "new": m.group(2).strip()}, 0.95)
    m = _ALWAYS.match(said)
    if m:
        return Command("always_spell", {"written": m.group(1).strip()}, 1.0)
    return None


# Deterministic list formats for the list commands.
def as_bullets(text: str, numbered: bool = False) -> str:
    parts = [p.strip(" ,.;") for p in re.split(r"(?<=[.!?।])\s+|\n+|;\s*|,\s+(?:and\s+)?", text or "") if p.strip(" ,.;")]
    return "\n".join((f"{i}. " if numbered else "- ") + p[:1].upper() + p[1:] for i, p in enumerate(parts, 1))


TRANSFORM_PROMPTS = {
    "formal": "Rewrite this in a clear, formal register. Keep every fact and name. Keep the language (Hindi stays Hindi, English stays English).",
    "shorter": "Make this shorter without losing any fact, name or request. Keep the language and tone.",
    "grammar": "Fix grammar, spelling and punctuation only. Change nothing else. Keep the language and script as written.",
    "english": "Translate this into natural English. Keep names as they are.",
    "hinglish": "Rewrite this in natural Hinglish written in Roman script, the way friends text. Keep names and facts.",
}
