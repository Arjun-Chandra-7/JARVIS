"""Speech as it was said → text as it was meant. Deterministic, fast, and careful with meaning.

    "um I think we should uh meet tomorrow"          → "I think we should meet tomorrow."
    "Buy eggs comma milk comma and bread"            → "Buy eggs, milk, and bread."
    "Tomorrow at five, actually make that six"       → "Tomorrow at six."
    "Tell him I'll come on Friday, no, Saturday"     → "Tell him I'll come on Saturday."
    "First point finish maths second point revise science" → "1. Finish maths\n2. Revise science"
    "आज electricity का chapter revise करना है"          → "आज electricity का chapter revise करना है।"

Rules it keeps:
* Never translates and never changes script. Hinglish stays Hinglish.
* A correction is only read as one when it is marked as one ("make that", "scratch that",
  "no," after a pause, "nahi, instead"). "I actually liked it" keeps its "actually".
* Repeated words are removed only where repetition is a stutter ("the the"), never where it is
  meaning ("very very", "bye bye", "jaldi jaldi").
* Fillers are the sounds people make while thinking — um, uh, hmm — not words like "like".
* Code and terminal profiles get literal treatment: no capitals, no full stop, symbols spoken
  as symbols ("dash dash help" → "--help").

This is the whole cleanup when no model is used, and always the first stage when one is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# --------------------------------------------------------------------------- fillers and stutters

_FILLERS = re.compile(r"(?i)(?<![\w'])(?:u+h+m*|u+m+|h+m+|e+r+m*|a+h+|mm+|uhh+|umm+)(?![\w'])[,.]?")

# Stutters happen on short function words; repetition of anything else is usually meant.
_STUTTER_WORDS = {"i", "the", "a", "an", "to", "and", "we", "is", "it", "of", "in", "that", "this",
                  "my", "you", "he", "she", "they", "for", "on", "at", "with", "but", "so", "if",
                  "main", "mai", "mein", "hum", "ko", "ki", "ka", "ke", "hai", "ye", "wo", "aur",
                  "मैं", "हम", "को", "की", "का", "के", "है", "ये", "वो", "और"}


def _destutter(text: str) -> str:
    def once(m: re.Match) -> str:
        return m.group(1) if m.group(1).lower() in _STUTTER_WORDS else m.group(0)
    return re.sub(r"(?i)(?<![\w'])([\w'ऀ-ॿ]+)(?:[\s,]+\1)+(?![\w'ऀ-ॿ])", once, text)


def _false_starts(text: str) -> str:
    """"I want to— I need to go" → "I need to go": a cut-off fragment restarted with its first word."""
    pattern = re.compile(r"(?i)(?<![\w'])((\w+)(?:\s+[\w']+){0,3})\s*(?:—|–|--|-)\s+(\2\b)")
    prev = None
    while prev != text:
        prev, text = text, pattern.sub(r"\3", text)
    return text


# --------------------------------------------------------------------------- corrections

# Marks that mean "replace what I just said with what follows".
_REPLACE_MARK = re.compile(
    r"(?i)\s*(?:[,;—–-]|\.{1,3})?\s*(?:"
    r"(?:no|nope|sorry|actually)[,]?\s+(?:make\s+(?:that|it)|i\s+mean|rather)|"
    r"make\s+that|i\s+mean|or\s+rather|correction|"
    r"nahi[,]?\s+(?:instead|balki)|nahi\s+nahi|isko\s+change\s+karke|"
    r"नहीं[,]?\s+(?:बल्कि|instead)"
    r")[,:]?\s+")
# "Friday—no, Saturday" / "five, sorry, six": a bare "no"/"sorry" only counts after a pause mark
# inside a sentence (not after a question: "Tea? No, coffee." is an answer, not a correction).
_SHORT_MARK = re.compile(r"(?i)(?<=[^?.!])\s*(?:[,—–-]|--)\s*(?:no|sorry|nahi)[,]?\s+")
# Not "actually": "I was tired, actually I slept early" is a sentence, not a correction.

# Marks that mean "drop what I just said".
_DROP_MARK = re.compile(
    r"(?i)\s*[,.;—–-]?\s*(?:scratch\s+that|delete\s+that|strike\s+that|forget\s+that|"
    r"remove\s+the\s+last\s+sentence|delete\s+the\s+last\s+sentence|ye\s+hatao|isko\s+hatao|"
    r"rehne\s+do|ये\s+हटाओ|इसे\s+हटाओ|रहने\s+दो)[.!,]?\s*")

_REPLACE_WITH = re.compile(r"(?i)\s*[,.]?\s*replace\s+(.+?)\s+with\s+(.+?)(?:[.!]|$)")


def _words(text: str) -> list[str]:
    return text.split()


def _apply_replacement(before: str, after: str) -> str:
    """Put ``after`` in place of the part of ``before`` it corrects."""
    before = before.rstrip(" ,;—–-")
    new = after.strip()
    if not before:
        return new
    new_words = _words(new)
    head = new_words[0].lower().strip(",.") if new_words else ""
    old_words = _words(before)
    # "meet at the cafe, make that meet at the library": it restates from a word already said.
    for i in range(len(old_words) - 1, -1, -1):
        if old_words[i].lower().strip(",.") == head and len(new_words) > 1:
            return " ".join(old_words[:i] + new_words)
    # "at five, make that six": a short correction replaces the same number of last words.
    if len(new_words) <= 3:
        # A number replaces a number; a weekday a weekday — the thing being corrected.
        kind = _kind(new_words[0])
        if kind:
            for i in range(len(old_words) - 1, -1, -1):
                if _kind(old_words[i]) == kind:
                    return " ".join(old_words[:i] + new_words + old_words[i + 1:])
        return " ".join(old_words[:max(0, len(old_words) - len(new_words))] + new_words)
    # Longer: it replaces the last clause.
    cut = max(before.rfind(","), before.rfind(";"))
    return (before[:cut + 1] + " " + new).strip() if cut > 0 else new


_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
             "somvar", "mangalvar", "budhvar", "guruvar", "shukravar", "shanivar", "ravivar"}
_NUMBERS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
            "twelve", "ek", "do", "teen", "char", "paanch", "chhe", "saat", "aath", "nau", "das"}
_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"}


_DAYS = {"today", "tomorrow", "tonight", "yesterday", "aaj", "kal", "parso", "आज", "कल", "परसों"}


def _kind(word: str) -> str:
    w = word.lower().strip(",.!?")
    if w in _WEEKDAYS:
        return "weekday"
    if w in _DAYS:
        return "day"
    if w in _MONTHS:
        return "month"
    if w in _NUMBERS or re.fullmatch(r"\d+(?::\d+)?(?:am|pm)?", w):
        return "number"
    return ""


def _corrections(text: str) -> str:
    # Drops first: "…tomorrow. Scratch that. Friday works" → "Friday works".
    while True:
        m = _DROP_MARK.search(text)
        if not m:
            break
        before, rest = text[:m.start()], text[m.end():]
        before = _drop_last_sentence(before)
        text = (before + " " + rest).strip()
    # Explicit replace
    while True:
        m = _REPLACE_WITH.search(text)
        if not m or m.start() == 0:
            break
        before, old, new = text[:m.start()], m.group(1).strip(), m.group(2).strip()
        idx = before.lower().rfind(old.lower())
        if idx < 0:
            break
        text = (before[:idx] + new + before[idx + len(old):] + " " + text[m.end():]).strip()
    for mark in (_REPLACE_MARK, _SHORT_MARK):
        while True:
            m = mark.search(text)
            if not m or m.start() == 0:
                break
            before, after = text[:m.start()], text[m.end():]
            # The correction runs to the end of its sentence.
            end = re.search(r"[.!?।]\s", after)
            fix, tail = (after[:end.start() + 1], after[end.start() + 1:]) if end else (after, "")
            if mark is _SHORT_MARK and len(_words(fix)) > 3:
                break                        # "…, no, I think we should…" is prose
            text = (_apply_replacement(before, fix.rstrip(".!?।")) + (fix[-1] if fix[-1:] in ".!?।" else "")
                    + tail).strip()
    return text


def _drop_last_sentence(text: str) -> str:
    text = text.rstrip(" ,;—–-")
    ends = [m.end() for m in re.finditer(r"[.!?।]\s", text)]
    return text[:ends[-1]].strip() if ends else ""


# --------------------------------------------------------------------------- spoken punctuation

_MARKS = [
    (r"\bnew\s+paragraph\b|\bnaya\s+paragraph\b|\bनया\s+पैराग्राफ\b", "\n\n"),
    (r"\bnew\s+line\b|\bnext\s+line\b|\bnayi\s+line\b|\bनई\s+लाइन\b", "\n"),
    (r"\bfull\s+stop\b|\bpoorn\s+viraam\b|\bपूर्ण\s+विराम\b", "."),
    (r"\bperiod\b(?=\s*$|\s+(?:new\s+(?:line|paragraph)))", "."),
    (r"\bcomma\b|\bअल्पविराम\b", ","),
    (r"\bquestion\s+mark\b", "?"),
    (r"\bexclamation\s+(?:mark|point)\b", "!"),
    (r"\bsemicolon\b", ";"),
    (r"\bcolon\b", ":"),
    (r"\bopen\s+(?:bracket|paren(?:thesis)?)\b", "("),
    (r"\bclose\s+(?:bracket|paren(?:thesis)?)\b", ")"),
    (r"\bopen\s+quotes?\b|\bquote\s+unquote\b", '"'),
    (r"\b(?:close|end)\s+quotes?\b|\bunquote\b", '"'),
    (r"\s*\bdot\s+(com|in|org|net|io|ai|dev|co)\b", r".\1"),
    # "arjun at gmail.com" → "arjun@gmail.com", once the dot is back
    (r"\b([\w.+-]+)\s+at\s+([\w-]+\.(?:com|in|org|net|io|ai|dev|co)(?:\.[a-z]{2})?)\b", r"\1@\2"),
]

# Literal-mode symbols (code, terminal): said as words, meant as characters.
_SYMBOLS = [
    (r"\bdash\s+dash\s*", "--"), (r"\bdouble\s+dash\s*", "--"), (r"\bdash\s*", "-"),
    (r"\bunderscore\b", "_"), (r"\bforward\s+slash\b|\bslash\b", "/"), (r"\bbackslash\b", "\\\\"),
    (r"\bpipe\b", "|"), (r"\btilde\b", "~"), (r"\bdollar\s+sign\b|\bdollar\b", "$"),
    (r"\bequals\s+sign\b|\bequals\b", "="), (r"\bdot\b", "."), (r"\bhash\b|\bhashtag\b", "#"),
    (r"\bampersand\b", "&"), (r"\bat\s+sign\b", "@"), (r"\bstar\b|\basterisk\b", "*"),
    (r"\bgreater\s+than\b", ">"), (r"\bless\s+than\b", "<"),
]


_GIT_VERBS = ("status|commit|push|pull|log|diff|add|checkout|switch|branch|clone|stash|rebase|merge|"
              "fetch|reset|restore|show|init|remote|tag")


def _shell_words(text: str) -> str:
    """Commands as a shell wants them: the recogniser capitalises the first word ("Ls", "Get")
    and hears "git" as "get" before a git verb."""
    text = re.sub(rf"(?i)^get\s+({_GIT_VERBS})\b", r"git \1", text)
    first = re.match(r"^([A-Z][a-z0-9_-]*)(?=\s|$)", text)
    if first:
        text = first.group(1).lower() + text[first.end():]
    return text.rstrip(".")


def _spoken_marks(text: str) -> str:
    for pattern, mark in _MARKS:
        text = re.sub(pattern, mark, text, flags=re.I)
    return _tidy_spaces(text)


def _symbols(text: str) -> str:
    for pattern, mark in _SYMBOLS:
        text = re.sub(pattern, mark, text, flags=re.I)
    text = re.sub(r"\s*([/_.=|~])\s*", r"\1", text)      # "src / main dot py" → "src/main.py"
    text = re.sub(r"(--?)\s+(?=\w)", r"\1", text)         # "-- help" → "--help"
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _tidy_spaces(text: str) -> str:
    text = re.sub(r"[ \t]+([.,;:!?)।])", r"\1", text)
    text = re.sub(r"([(])[ \t]+", r"\1", text)
    # A space after punctuation — but not inside "gmail.com", "main.py" or "3.14".
    text = re.sub(r"([,;!?।])(?=[^\s\d\"')\].,;:!?।])", r"\1 ", text)
    text = re.sub(r"([.:])(?=[A-Zऀ-ॿ])", r"\1 ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"([,;:])\1+", r"\1", text)
    text = re.sub(r",\s*([.!?।])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# --------------------------------------------------------------------------- lists

_ORDINAL = r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|next|last|pehla|doosra|dusra|teesra|tisra|chautha)"
_LIST_ITEM = re.compile(
    rf"(?i)(?:^|[\s,.;:])(?:{_ORDINAL}\s+point|point\s+(?:number\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)|"
    rf"number\s+(?:\d+|one|two|three|four|five|six)|bullet(?:\s+point)?)[,:.]?\s+")


def _lists(text: str) -> str:
    marks = list(_LIST_ITEM.finditer(text))
    if len(marks) < 2:
        return text
    intro = text[:marks[0].start()].strip(" ,.;:")
    bullet = "bullet" in marks[0].group(0).lower()
    items = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        item = text[m.end():end].strip(" ,.;")
        if item:
            items.append(item[:1].upper() + item[1:])
    lines = [("- " if bullet else f"{n}. ") + item for n, item in enumerate(items, 1)]
    return ((intro[:1].upper() + intro[1:] + ":\n") if intro else "") + "\n".join(lines)


# --------------------------------------------------------------------------- dictionary

def _dictionary(text: str, entries) -> str:
    for spoken, written in entries:
        text = re.sub(rf"(?i)(?<![\w]){re.escape(spoken)}(?![\w])", written, text)
    return text


# --------------------------------------------------------------------------- shape

def _sentence_case(text: str) -> str:
    text = re.sub(r"(?<![\w'])i(?=['\s,.!?]|$)", "I", text)
    def up(m: re.Match) -> str:
        return m.group(1) + m.group(2).upper()
    text = re.sub(r"(^|[.!?]\s+|\n+)([a-z])", up, text)
    return text


def _end_sentence(text: str, messaging: bool) -> str:
    if not text or text[-1] in ".!?।:;\"')" or text.endswith("\n"):
        return text
    last_line = text.rsplit("\n", 1)[-1]
    if re.match(r"^\s*(?:\d+\.|-)\s", last_line):
        return text                        # a list item needs no full stop
    if messaging and "\n" not in text and len(re.findall(r"[.!?।]", text)) == 0:
        return text                        # one line in a chat: no formal full stop
    if _DEVANAGARI.search(last_line[-3:] or ""):
        return text + "।"
    if len(text.split()) < 3 and not messaging:
        return text
    return text + "."


@dataclass
class Cleaned:
    text: str
    raw: str
    changes: list[str] = field(default_factory=list)


def clean(raw: str, profile: str = "prose", dictionary=(), literal: Optional[bool] = None) -> Cleaned:
    """``profile`` is one of prose, messaging, email, document, code, terminal, search."""
    literal = profile in {"code", "terminal"} if literal is None else literal
    text = re.sub(r"\s+", " ", (raw or "").strip())
    changes = []

    def step(name, fn, *a):
        nonlocal text
        new = fn(text, *a)
        if new != text:
            changes.append(name)
            text = new

    step("fillers", lambda t: re.sub(r"\s{2,}", " ", _FILLERS.sub("", t)).strip(" ,"))
    step("false starts", _false_starts)
    step("stutters", _destutter)
    step("corrections", _corrections)
    step("dictionary", _dictionary, list(dictionary))
    if literal:
        step("symbols", _symbols)
        if profile == "terminal":
            step("commands", _shell_words)
        return Cleaned(text=text.strip(), raw=raw, changes=changes)
    step("punctuation", _spoken_marks)
    step("lists", _lists)
    if profile == "search":
        step("search", lambda t: re.sub(r"(?i)^(?:search\s+(?:for\s+)?|look\s+up\s+|google\s+)", "", t).rstrip(".!।"))
        return Cleaned(text=text, raw=raw, changes=changes)
    step("capitals", _sentence_case)
    step("ending", _end_sentence, profile == "messaging")
    return Cleaned(text=text.strip(), raw=raw, changes=changes)
