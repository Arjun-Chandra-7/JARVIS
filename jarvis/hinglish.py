"""Hindi and Hinglish commands, rewritten into the English the rest of Jarvis already understands.

One translation layer, applied before anything else looks at the text, so every handler that
already works — «open X», volume, brightness, the screen clicks — works in Hindi without being
touched. The alternative, teaching each handler two languages, multiplies every future command by
two and guarantees they drift apart.

Hindi puts the verb last. "YouTube kholo" is object-then-verb where English is verb-then-object,
so this is a reordering rather than a word swap, and that is most of what these patterns do.

Both scripts are accepted: people type and speak Hinglish in Latin letters, and Whisper returns
Devanagari when it is confident the speech was Hindi, so the same sentence arrives either way.
"""

from __future__ import annotations

import re

# --- verbs, as they arrive at the end of a sentence -------------------------------------------
# Each maps to the English imperative the existing handlers match on.
_OPEN = r"(?:kholo|khol\s*do|kholiye|khol|chalu\s*karo|chaalu\s*karo|start\s*karo|खोलो|खोल\s*दो|चालू\s*करो)"
_PLAY = r"(?:bajao|baja\s*do|chalao|chala\s*do|lagao|laga\s*do|बजाओ|चलाओ|लगाओ)"
_CLOSE = r"(?:band\s*karo|band\s*kar\s*do|bandh\s*karo|बंद\s*करो|बंद\s*कर\s*दो)"
_SEARCH = r"(?:dhoondo|dhundo|khojo|search\s*karo|ढूंढो|खोजो)"
_CLICK = r"(?:click\s*karo|dabao|daba\s*do|press\s*karo|क्लिक\s*करो|दबाओ)"
_SHOW = r"(?:dikhao|dikha\s*do|batao|bata\s*do|दिखाओ|बताओ)"

# --- things that have a level ------------------------------------------------------------------
_VOLUME = r"(?:awaaz|awaz|aawaz|volume|sound|आवाज़|आवाज|वॉल्यूम)"
_BRIGHT = r"(?:brightness|roshni|roshini|chamak|ujala|रोशनी|चमक|ब्राइटनेस)"
_UP = r"(?:badhao|badha\s*do|tez\s*karo|zyada\s*karo|upar\s*karo|बढ़ाओ|तेज़\s*करो|ज़्यादा\s*करो)"
_DOWN = r"(?:kam\s*karo|kam\s*kar\s*do|ghatao|dheere\s*karo|neeche\s*karo|कम\s*करो|घटाओ|धीरे\s*करो)"

# Politeness and filler that carries no instruction. Stripped before matching so "zara YouTube
# khol do na" reduces to the same shape as "YouTube kholo".
_FILLER = re.compile(
    r"\b(?:zara|zara\s*sa|thoda|thodi|please|plz|yaar|bhai|na|to|toh|ji|abhi|jaldi|"
    r"ek\s*baar|mere\s*liye|ज़रा|थोड़ा|प्लीज़|यार|भाई|अभी|जल्दी)\b",
    re.IGNORECASE,
)

# "mera", "meri", "is", "us" — possessives and demonstratives that do not change the target.
_DET = re.compile(r"\b(?:mera|meri|mere|ye|yeh|woh|wo|is|us|मेरा|मेरी|मेरे|ये|वो)\b", re.IGNORECASE)

_NUM = r"(?P<value>\d{1,3})"


def _clean(text: str) -> str:
    text = _FILLER.sub(" ", text or "")
    text = _DET.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,.!?")


# Order matters: the most specific shape wins, and levels are checked before bare verbs so
# "awaaz 50 kar do" is not read as "open 50".
_RULES: list[tuple[re.Pattern, str]] = [
    # "awaaz 50 kar do", "volume 50 karo", "brightness 30 kar do"
    (re.compile(rf"^{_VOLUME}\s+{_NUM}\s*(?:%|percent|pratishat)?\s*(?:kar\s*do|karo|kardo|कर\s*दो|करो)?$", re.I),
     "set the volume to {value}"),
    (re.compile(rf"^{_BRIGHT}\s+{_NUM}\s*(?:%|percent|pratishat)?\s*(?:kar\s*do|karo|kardo|कर\s*दो|करो)?$", re.I),
     "set the brightness to {value}"),

    # "awaaz badhao", "brightness kam karo"
    (re.compile(rf"^{_VOLUME}\s+{_UP}$", re.I), "turn the volume up"),
    (re.compile(rf"^{_VOLUME}\s+{_DOWN}$", re.I), "turn the volume down"),
    (re.compile(rf"^{_BRIGHT}\s+{_UP}$", re.I), "turn the brightness up"),
    (re.compile(rf"^{_BRIGHT}\s+{_DOWN}$", re.I), "turn the brightness down"),

    # "awaaz kitni hai", "brightness kya hai"
    (re.compile(rf"^{_VOLUME}\s+(?:kitni|kitna|kya)\s*(?:hai)?\??$", re.I), "what is the volume"),
    (re.compile(rf"^{_BRIGHT}\s+(?:kitni|kitna|kya)\s*(?:hai)?\??$", re.I), "what is the brightness"),

    # "awaaz band karo" is mute, not "close the volume".
    (re.compile(rf"^{_VOLUME}\s+{_CLOSE}$", re.I), "mute"),
    (re.compile(r"^(?:mute\s*karo|chup\s*karo|म्यूट\s*करो|चुप\s*करो)$", re.I), "mute"),
    (re.compile(r"^(?:unmute\s*karo|awaaz\s*wapas|अनम्यूट\s*करो)$", re.I), "unmute"),

    # "<target> par/pe <thing> chalao"  →  play <thing> on <target>
    (re.compile(rf"^(?P<where>.+?)\s+(?:par|pe|pr|में|पर)\s+(?P<what>.+?)\s+{_PLAY}$", re.I),
     "play {what} on {where}"),
    (re.compile(rf"^(?P<where>.+?)\s+(?:par|pe|pr|में|पर)\s+(?P<what>.+?)\s+{_SEARCH}$", re.I),
     "search {what} on {where}"),

    # "YouTube kholo", "Netflix chalu karo"
    (re.compile(rf"^(?P<target>.+?)\s+{_OPEN}$", re.I), "open {target}"),
    (re.compile(rf"^(?P<target>.+?)\s+{_PLAY}$", re.I), "play {target}"),
    (re.compile(rf"^(?P<target>.+?)\s+{_CLOSE}$", re.I), "close {target}"),
    (re.compile(rf"^(?P<target>.+?)\s+{_SEARCH}$", re.I), "search for {target}"),

    # "allow button pe click karo", "allow dabao"
    (re.compile(rf"^(?P<target>.+?)\s+(?:par|pe|pr|पर)\s+{_CLICK}$", re.I),
     "click {target} on my screen"),
    (re.compile(rf"^(?P<target>.+?)\s+{_CLICK}$", re.I), "click {target} on my screen"),

    # "screenshot dikhao", "calendar dikhao"
    (re.compile(rf"^(?P<target>.+?)\s+{_SHOW}$", re.I), "show me {target}"),

    # Verb first. Hindi normally ends on the verb, but an imperative can lead — "खोलो YouTube",
    # "chalao Netflix" — and Whisper returns whichever order was spoken.
    (re.compile(rf"^{_OPEN}\s+(?P<target>.+)$", re.I), "open {target}"),
    (re.compile(rf"^{_PLAY}\s+(?P<target>.+)$", re.I), "play {target}"),
    (re.compile(rf"^{_CLOSE}\s+(?P<target>.+)$", re.I), "close {target}"),
    (re.compile(rf"^{_SEARCH}\s+(?P<target>.+)$", re.I), "search for {target}"),
    (re.compile(rf"^{_SHOW}\s+(?P<target>.+)$", re.I), "show me {target}"),
]

# Cheap pre-check: if none of these appear the sentence is not Hindi and nothing below can match.
# The verbs alone were not enough — "volume 50 kar do" and "mute karo" carry the instruction in
# "kar do" and "karo", and "awaaz kitni hai" has no verb at all. Deliberately excludes bare
# English words like "volume", so an English sentence never takes this path.
_MARKERS = r"(?:kar\s*do|kardo|karo|kitni|kitna|hai|awaaz|awaz|aawaz|roshni|roshini|chamak|ujala)"
_ANY_HINDI = re.compile(
    "|".join([_OPEN, _PLAY, _CLOSE, _SEARCH, _CLICK, _SHOW, _UP, _DOWN, _MARKERS,
              r"[\u0900-\u097f]"]),
    re.IGNORECASE,
)


def looks_hindi(text: str) -> bool:
    return bool(_ANY_HINDI.search(text or ""))


def normalise(text: str) -> str:
    """The English equivalent of a Hindi or Hinglish command, or the text unchanged.

    Unchanged is the important half: an English sentence must pass through untouched, and a Hindi
    sentence this does not recognise is better handed to the model as it was said than mangled
    into something that looks like a command and is not.
    """
    raw = (text or "").strip()
    if not raw or not looks_hindi(raw):
        return raw
    cleaned = _clean(raw)
    if not cleaned:
        return raw
    for pattern, template in _RULES:
        match = pattern.match(cleaned)
        if not match:
            continue
        parts = {k: (v or "").strip() for k, v in match.groupdict().items()}
        if any(not v for v in parts.values()):
            continue
        return template.format(**parts)
    return raw
