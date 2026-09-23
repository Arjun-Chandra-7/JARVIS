"""Questions about a topic — "what is a sequential input in an RNN?", "photosynthesis kya hota hai",
"explain Ohm's law" — answered by a tutor on the strong model, not by the small local brain.

Heard live: "What is a sequential input or sequential output in an RNN?" was answered by the local
3B model in one flat sentence, and "give me some examples" after it by another. A topic question
now gets the idea in plain words, how it works with a concrete example, and a one-line takeaway,
in the language it was asked in; and a follow-up ("give me examples", "explain simpler", "hindi
mein samjhao") within ten minutes continues the same topic.

Only topic questions: anything about the person, the machine, the time, the weather, a file or a
message is not a topic, and goes where it went before. With no strong model this passes, and the
normal brain answers.
"""
from __future__ import annotations

import re
import time
from typing import Optional

_LEAD = r"(?:(?:so|ok(?:ay)?|and|hey\s+jarvis|jarvis|please|can\s+you|could\s+you|now)[,\s]+)*"
_ASK = re.compile(
    rf"""(?ix)^{_LEAD}(?:
        what\s+(?:is|are|was|were)\s+(?P<t1>.+) |
        what'?s\s+(?P<t1b>.+) |
        (?:explain|describe|define|teach\s+me(?:\s+about)?|tell\s+me\s+about)\s+(?P<t2>.+) |
        why\s+(?:is|are|does|do|did)\s+(?P<t3>.+) |
        how\s+(?:does|do|is|are|can)\s+(?P<t4>(?!i\b|we\b|you\b).+) |
        (?P<t6>.+?)\s+(?:kya\s+(?:hota|hoti|hote)\s+(?:hai|hain)|kya\s+hai|ko\s+samjhao|samjhao|
                        explain\s+karo|ka\s+matlab\s+kya\s+hai|kaise\s+kaam\s+karta\s+hai) |
        (?P<t7>.+?)\s*(?:क्या\s+(?:होता|होती|होते)\s+(?:है|हैं)|क्या\s+है|को\s+समझाओ|समझाओ|का\s+मतलब\s+क्या\s+है)
    )[\s?.!।]*$""")

# Not topics: the person, the machine, live facts, and Jarvis's own features.
_NOT_A_TOPIC = re.compile(
    r"(?i)\b(?:my|mine|your|me|i|time|date|day|today|tonight|tomorrow|yesterday|weather|temperature|"
    r"battery|volume|brightness|calendar|schedule|status|news|price|score|stock|playing|song|music|"
    r"email|mail|message|messages|whatsapp|file|folder|screen|this|that|it|jarvis|claude|terminal|agent|"
    r"job|task|reminder|timer|alarm|wifi|wi-fi|bluetooth|cpu|ram|disk|internet|ip|location|traffic|mode|"
    r"going\s+on|up|wrong|happening|the\s+matter|new|latest|current|recent|now|mera|meri|mere|ye|yeh|isko|iska|मेरा|मेरी|ये|यह)\b")

_FOLLOW = re.compile(
    r"(?ix)^(?:so\s+|and\s+|ok(?:ay)?\s+)*(?:"
    r"give\s+me\s+(?:some\s+|an?\s+|more\s+)?examples?|(?:an?\s+)?examples?\s*(?:please|do|batao)?|"
    r"(?:explain|say)\s+(?:it\s+|that\s+)?(?:again|more|simpler|more\s+simply|in\s+(?:hindi|hinglish|english|detail))|"
    r"elaborate|go\s+deeper|tell\s+me\s+more|why(?:\s+is\s+that)?|how\s+so|"
    r"(?:aur|thoda)\s+(?:samjhao|detail\s+mein)|simple\s+(?:mein|me)\s+samjhao|(?:hindi|hinglish)\s+(?:mein|me)\s+samjhao|"
    r"example\s+(?:do|batao|dikhao)|उदाहरण\s+(?:दो|बताओ)|और\s+समझाओ)[\s?.!।]*$")

_SYSTEM = (
    "You are a warm, clear tutor. Explain the topic the way a good teacher talks: the idea in one "
    "plain sentence, how it works with one small concrete example, then a one-line takeaway. If it "
    "is school material, pitch it at a CBSE Class 10 student. Spoken aloud: short sentences, no "
    "markdown or bullet symbols, formulas in words. About 160 words at most.")

FOLLOW_UP_S = 600.0
_LAST: dict[str, tuple[float, str, str]] = {}      # session → (when, question, answer)


def topic(text: str) -> Optional[str]:
    said = re.sub(r"(?i)^(?:hey\s+)?jarvis[,.!\s]*", "", (text or "").strip())
    m = _ASK.match(said)
    if not m:
        return None
    subject = next((g for g in m.groups() if g), "").strip(" ?.!,")
    if not subject or len(subject.split()) > 14 or _NOT_A_TOPIC.search(subject):
        return None
    return subject


def is_follow_up(text: str, session: str, now: Optional[float] = None) -> bool:
    last = _LAST.get(session)
    return bool(last and (now or time.time()) - last[0] < FOLLOW_UP_S and _FOLLOW.match((text or "").strip()))


async def handle(text: str, config=None) -> Optional[str]:
    from . import context, route_log
    from .llm import complete_detailed
    from .video_command import _LANGUAGE_INSTRUCTION, reply_language

    session = context.current()
    follow = is_follow_up(text, session)
    subject = None if follow else topic(text)
    if not follow and not subject:
        return None
    lang = reply_language(text)
    prompt = f"They asked: \"{text.strip()}\"\n{_LANGUAGE_INSTRUCTION[lang]}"
    if follow:
        _t, question, answer = _LAST[session]
        prompt = (f"Earlier they asked: \"{question}\"\nYou explained: \"{answer[:600]}\"\n"
                  f"Now they say: \"{text.strip()}\" — continue on the same topic.\n{_LANGUAGE_INSTRUCTION[lang]}")
    done = await complete_detailed(_SYSTEM, prompt, config, temperature=0.4, strength="strong")
    route_log.record(intent="teach", action="explain" if done.ok else "no_strong_model", lang=lang,
                     follow_up=follow, memory="not_consulted")
    if not done.ok:
        return None                                # the normal brain answers instead
    _LAST[session] = (time.time(), _LAST[session][1] if follow else text.strip(), done.text)
    return done.text
