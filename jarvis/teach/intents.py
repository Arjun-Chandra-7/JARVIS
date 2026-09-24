"""What a request to the teaching overlay means — in English, Hinglish, or Hindi.

Three kinds of thing are recognised here, and nothing else:

  * a lesson: "pause and explain this step visually", "explain RAG architecture with a diagram",
    "isko diagram se samjhao", "पाइथागोरस चित्र बनाकर समझाओ";
  * a control of the lesson or drawing in front of you: pause, continue, go back, skip, again,
    clear, leave it, undo, redo, bigger, move left, pen mode…;
  * a drawing: "draw a triangle", "circle this", "label this as X", "arrow from this to that".

Controls only mean something while there is a lesson or a drawing to control — "continue" with
nothing on the overlay is somebody else's "continue" — so the caller asks with ``active``.
Everything returns an ``Intent`` or None; None always means "not ours", and the request goes on
to whatever handled it before this existed.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .lessons import rag


@dataclass
class Intent:
    kind: str                         # "lesson" | "control" | "draw" | "follow_up"
    name: str                         # e.g. "screen", "topic", "pause", "circle", "explain"
    topic: str = ""                   # "pythagoras" | "rag" | "" (from the screen)
    args: dict = field(default_factory=dict)


_LEAD = re.compile(r"(?i)^\s*(?:(?:hey|ok|okay|so|and|now|please|jarvis|जार्विस)[\s,.!]+)*")
_TAIL = re.compile(r"(?:[\s,.!?।]+(?:please|plz|sir|jarvis|na|zara|ज़रा|प्लीज़))?[\s,.!?।]*$", re.I)


def _clean(text: str) -> str:
    s = _LEAD.sub("", (text or "").strip())
    s = _TAIL.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- lessons
_VISUAL = (r"visual(?:ly|s)?|with\s+(?:a\s+)?(?:diagram|drawing|picture|figure|sketch)|on\s+(?:the\s+)?screen|"
           r"draw(?:ing)?|diagram|sketch|figure\s+(?:it\s+)?out\s+visually|"
           r"diagram\s+(?:se|bana\s*ke|banakar|bana\s*kar)|draw\s+kar(?:ke|\s*ke)?|bana\s*ke|banakar|bana\s*kar|dikha\s*ke|dikhakar|"
           r"चित्र|डायग्राम|diagram\s+से|बनाकर|बना\s*के|दिखाकर|दिखा\s*के|draw\s+करके")
_EXPLAIN = (r"explain|teach|show\s+me|walk\s+me\s+through|break\s+(?:it|this)\s+down|"
            r"samjha(?:o|iye|na|do|de)|samjhao|sikhao|batao|dikhao|"
            r"समझा(?:ओ|इए|ना|दो|दे)|सिखाओ|बताओ|दिखाओ")
_THIS = (r"this|that|it|the\s+(?:current\s+)?(?:step|part|video|screen|slide|frame|diagram)|what(?:'s|\s+is)\s+on\s+(?:the\s+)?screen|"
         r"is(?:ko|e|ka)?|ye(?:h)?|yahan|abhi\s+wala|इसे|इसको|इसका|यह|ये|यहाँ|अभी\s+वाला")
_SCREEN_LESSON = re.compile(rf"(?ix)(?:\bpause\b.*\b(?:{_EXPLAIN})|\b(?:{_EXPLAIN})\b|\b(?:{_THIS})\b)")
_TOPICS = {
    "pythagoras": re.compile(r"(?i)pyth?ag[oa]r[ae]?s|pythagorean|hypotenuse|right[\s-]*angled?\s+triangle|a\s*(?:²|\^?2|square[ds]?)\s*\+\s*b|"
                             r"पाइथागोरस|पायथागोरस|कर्ण|समकोण\s*त्रिभुज|pythagoras\s+theorem|pythagoras\s+ka"),
    "rag": re.compile(r"(?i)\brag\b|\br\.a\.g\b|retrieval[\s-]*augmented|retrieval\s+augment|आरएजी|रैग"),
}


def topic_of(text: str) -> str:
    for name, rx in _TOPICS.items():
        if rx.search(text or ""):
            return name
    return ""


_LANG_TAG = (r"(?:in\s+)?(?:pure\s+|shuddh\s+)?(?:english|hindi|hinglish)(?:\s+(?:mein|me|main))?|"
             r"(?:शुद्ध\s+)?(?:हिंदी|हिन्दी|अंग्रेज़ी|अंग्रेजी|english)\s*(?:में|मे)?")
_FILLER = (r"can|could|would|you|please|me|to\s+me|for\s+me|us|how|what|the\s+idea\s+of|about|and|with|using|"
           r"a|an|the|of|it|ko|ka|ki|ke|को|का|की|के|में|मे|se|से|karke|kar\s*ke|करके|kar\s+do|करो|do|dijiye|and\s+explain|"
           r"works?|happens?|kaise|कैसे|kaam\s+karta\s+hai|काम\s+करता\s+है|hota\s+hai|होता\s+है|hai|है")
_DEICTIC = re.compile(rf"(?ix)^(?:{_THIS}|इसे|इसको|इसका|यह|ये|यहाँ|(?:this|the)\s+(?:step|part|thing|topic|concept|screen|video|page|slide|chapter|diagram|question|problem))$")
_VISUAL_RX = re.compile(rf"(?i)(?:{_VISUAL}|(?:a\s+)?(?:flow\s*chart|flowchart|mind\s*map|picture|figure))")
_EXPLAIN_RX = re.compile(rf"(?i)(?:\b(?:{_EXPLAIN})\b|समझा\w*|सिखा\w*|बताओ|दिखाओ|explain\s+(?:karo|kijiye)|\bvisuali[sz]e\b|"
                         r"\bdiagram\s+of\b|\bdraw\s+(?:out\s+)?(?:a\s+)?(?:diagram|flow\s*chart|flowchart|mind\s*map)\b)")


def subject_of(text: str) -> str:
    """What the lesson is about: the request with the asking, the drawing and the language removed.
    "Explain the water cycle with a diagram" → "water cycle"; "इसे चित्र बनाकर समझाओ" → "इसे"."""
    t = _clean(text)
    t = re.sub(r"(?i)\bpause\s+(?:and|karke|kar\s+ke)\b|\bpause\b|रोककर|रोक\s+कर", " ", t)
    t = _VISUAL_RX.sub(" ", t)
    t = _EXPLAIN_RX.sub(" ", t)
    t = re.sub(rf"(?i)(?:{_LANG_TAG})", " ", t)
    t = re.sub(r"(?i)\b(?:step\s+by\s+step|in\s+detail|simply|clearly|properly|quickly|again)\b", " ", t)
    words = [w for w in re.split(r"[\s,.!?।]+", t) if w]
    filler = re.compile(rf"(?ix)^(?:{_FILLER})$")
    while words and filler.match(words[0]):
        words.pop(0)
    while words and filler.match(words[-1]):
        words.pop()
    return " ".join(words).strip()


# "Explain the topic on my screen", "teach me this chapter", "screen wala topic samjhao",
# "स्क्रीन पर जो है वो समझाओ": asking to be taught what is on screen is asked the way a student asks a
# teacher, and a teacher draws. These become drawn lessons without anyone saying "diagram". A
# specific question about the screen ("why were they reluctant…?") is not one of them — that is
# answered in words.
_TEACH_VERB = (r"explain|teach(?:\s+me)?|walk\s+me\s+through|break\s+down|help\s+me\s+understand|"
               r"samjha(?:o|iye|do|na)|sikha(?:o|iye)|padhao|padha\s+do|समझा(?:ओ|इए|दो|ना)|सिखा(?:ओ|इए)|पढ़ा(?:ओ|इए|\s+दो)")
_SCREEN_TOPIC = (r"(?:the\s+|this\s+)?(?:topic|chapter|concept|lesson|step|part|section|question|problem|slide|page|diagram|video|lecture|thing)"
                 r"(?:\s+(?:that'?s|which\s+is|that\s+is))?\s+(?:on|in)\s+(?:my|the)\s+screen|"
                 r"(?:what(?:'s|\s+is)|whatever(?:'s|\s+is)?)\s+on\s+(?:my|the)\s+screen|"
                 r"this\s+(?:topic|chapter|concept|lesson|step|lecture)|"
                 r"(?:screen|स्क्रीन)\s+(?:pe|par|पे|पर)\s+(?:wala|wali|jo|वाला|वाली|जो)\b.*|"
                 r"(?:ye|yeh|is)\s+(?:topic|chapter|concept|step)(?:\s+ko)?|(?:यह|ये|इस)\s+(?:topic|chapter|अध्याय|पाठ|step)(?:\s+को)?")
_TEACH_SCREEN = re.compile(
    rf"(?ix)^(?:(?:{_TEACH_VERB})\s+(?:me\s+)?(?:{_SCREEN_TOPIC})|(?:{_SCREEN_TOPIC})\s+(?:ko\s+|को\s+)?(?:{_TEACH_VERB}))"
    rf"(?:\s+(?:to\s+me|please|properly|simply|like\s+a\s+teacher|in\s+(?:english|hindi|hinglish)|(?:english|hindi|hinglish)\s+(?:mein|me|में)))*$")


# Any "explain <anything> on my screen", however speech recognition bends the verb. From the log:
# "I explained the poem on my screen.", "explained the part of the poem on my screen." and
# "Explain me the poem on my screen." all missed, because only a fixed list of nouns was accepted
# and "explained" is not "explain". A specific question ("why were they reluctant…") is excluded.
_EXPLAIN_FORMS = (r"(?:i\s+)?(?:explain(?:ed|s|ing)?|teach(?:es|ing)?|taught|describe|break\s+down|walk\s+me\s+through|"
                  r"help\s+me\s+understand|samjha\w*|sikha\w*|padha\w*|समझा\w*|सिखा\w*|पढ़ा\w*)")
_ON_SCREEN = r"(?:on|in|from)\s+(?:my|the|this)\s+screen|(?:screen|स्क्रीन)\s+(?:pe|par|पे|पर)(?:\s+(?:wala|wali|jo|वाला|वाली|जो))?"
_ANY_ON_SCREEN = re.compile(
    rf"(?ix)^(?:(?:jarvis|hey)[\s,]+)*(?:(?:{_ON_SCREEN})[\s,]+)?{_EXPLAIN_FORMS}\b(?:\s+(?:me|to\s+me|us))?"
    rf"(?:\s+(?!why\b|how\b|who\b|when\b|which\b)\S+){{0,10}}?\s*(?:(?:{_ON_SCREEN})\b.*)?$")
_SPECIFIC_Q = re.compile(r"(?i)\b(?:why|how\s+come|who|when|which|what\s+does|what\s+did|what\s+values|also)\b|\?")


def teaches_the_screen(s: str) -> bool:
    if _TEACH_SCREEN.match(s):
        return True
    if not re.search(rf"(?i){_ON_SCREEN}", s) or _SPECIFIC_Q.search(s):
        return False
    return bool(_ANY_ON_SCREEN.match(s))


def lesson(text: str) -> Optional[Intent]:
    """A request for a drawn lesson — on a named subject, or on whatever is on the screen."""
    s = _clean(text)
    if not s:
        return None
    if teaches_the_screen(s) and os.environ.get("JARVIS_VISUAL_EXPLAIN", "1") != "0":
        # Asked to be taught, not asked for a picture: if the drawing can't be made, the spoken
        # explanation still happens (``auto`` tells the caller to fall back rather than refuse).
        return Intent("lesson", "screen", topic_of(s), {"pause_first": bool(re.search(r"(?i)\bpause\b", s)), "auto": True})
    visual = _VISUAL_RX.search(s)
    explain = _EXPLAIN_RX.search(s)
    pause_first = bool(re.search(r"(?i)\bpause\b|\bruko\b|रोको|रोककर|pause\s+kar", s))
    if not visual or not (explain or pause_first):
        return None
    subject = subject_of(s)
    topic = topic_of(s)
    # "The poem on the screen" is the poem on the screen — not a lesson about poems. Found in the
    # log: it drew stanzas and rhyme in general while the poem itself sat unexplained.
    if not subject or _DEICTIC.match(subject) or len(subject) < 2 or re.search(rf"(?i){_ON_SCREEN}", subject):
        return Intent("lesson", "screen", topic, {"pause_first": pause_first})
    if len(subject.split()) > 12:
        return None                           # a sentence, not a subject: leave it to the tutor
    return Intent("lesson", "topic", topic, {"pause_first": pause_first, "subject": subject})


# --------------------------------------------------------------------------- controls
_CONTROLS = [
    ("pause", r"pause(?:\s+(?:the\s+)?(?:explanation|lesson|it|there))?|hold\s+on|wait(?:\s+a\s+(?:sec(?:ond)?|moment|minute))?|"
              r"ruko|ruk\s+jao|ek\s+(?:second|minute)|रुको|रुक\s+जाओ|ठहरो"),
    ("continue", r"continue|go\s+on|carry\s+on|keep\s+going|resume|go\s+ahead|next|"
                 r"aage(?:\s+(?:badho|chalo|bolo|batao))?|chalo\s+aage|jaari\s+rakho|आगे(?:\s+(?:बढ़ो|चलो|बोलो|बताओ))?|जारी\s+रखो"),
    ("back", r"(?:go\s+)?back(?:\s+(?:one|a)\s+step)?|previous\s+step|one\s+step\s+back|last\s+step\s+again|"
             r"peeche(?:\s+(?:jao|chalo))?|pichla\s+step|ek\s+step\s+peeche|पीछे(?:\s+(?:जाओ|चलो))?|पिछला\s+step|पिछला\s+चरण"),
    ("skip", r"skip(?:\s+(?:this|that|the)\s+step)?|skip\s+it|next\s+step|chhodo|skip\s+karo|छोड़ो|अगला\s+step|अगला\s+चरण"),
    ("again", r"(?:explain|say|show)\s+(?:that|this|it)\s+again|again|repeat(?:\s+that)?|one\s+more\s+time|"
              r"phir\s+se(?:\s+(?:samjhao|batao|bolo))?|dobara(?:\s+samjhao)?|फिर\s+से(?:\s+समझाओ)?|दोबारा(?:\s+समझाओ)?"),
    ("clear", r"clear(?:\s+(?:it|that|this|everything|all))?|clear\s+the\s+(?:screen|overlay|drawing|diagram|board)|"
              r"remove\s+(?:the\s+)?(?:diagram|drawing|overlay|lesson|it)|erase\s+(?:everything|all|the\s+screen)|wipe\s+it|"
              r"(?:sab\s+)?(?:hata\s+do|hatao|mita\s+do|saaf\s+karo)|diagram\s+hatao|साफ़\s+करो|साफ\s+करो|हटा\s+दो|हटाओ|मिटा\s+दो"),
    ("clear_mine", r"clear\s+my\s+(?:drawing|ink|pen)|erase\s+my\s+(?:drawing|ink)|meri\s+drawing\s+hatao"),
    ("hide", r"hide\s+(?:the\s+)?overlay|hide\s+(?:it|the\s+drawing|the\s+diagram)|overlay\s+(?:band|chhupao)|छुपाओ"),
    ("leave", r"leave\s+(?:it|the\s+diagram|the\s+drawing|that)(?:\s+(?:there|on\s+screen|up))?|keep\s+(?:it|the\s+diagram|that)(?:\s+(?:there|on\s+screen|up))?|"
              r"don'?t\s+clear(?:\s+it)?|rehne\s+do|rakho\s+(?:isko|ise)|रहने\s+दो|रखो"),
    ("undo", r"undo(?:\s+(?:that|it|the\s+last\s+one))?|take\s+that\s+back|wapas\s+karo|अनडू"),
    ("redo", r"redo(?:\s+(?:that|it))?"),
    ("bigger", r"(?:make\s+(?:it|this|that|the\s+diagram)\s+)?(?:bigger|larger)|zoom\s+in|enlarge(?:\s+it)?|bada\s+karo|बड़ा\s+करो"),
    ("smaller", r"(?:make\s+(?:it|this|that|the\s+diagram)\s+)?smaller|zoom\s+out|shrink(?:\s+it)?|chhota\s+karo|छोटा\s+करो"),
    ("move", r"move\s+(?:it|this|that|the\s+diagram)\s+(?:to\s+the\s+)?(?P<dir>left|right|up|down)|(?:left|right)\s+(?:mein|me)\s+karo|"
             r"(?P<dir2>left|right|upar|neeche)\s+(?:karo|le\s+jao)"),
    ("pen_on", r"(?:open|start|turn\s+on|enable)?\s*(?:the\s+)?pen(?:\s+mode)?(?:\s+on)?|let\s+me\s+draw|i(?:'ll|\s+will)\s+draw|manual\s+(?:pen|drawing)|"
               r"main\s+draw\s+karta\s+hoon|pen\s+(?:do|chalu\s+karo)|मैं\s+बनाता\s+हूँ"),
    ("pen_off", r"(?:close|stop|exit|turn\s+off|disable)\s+(?:the\s+)?pen(?:\s+mode)?|pen\s+(?:mode\s+)?off|done\s+drawing|pen\s+band\s+karo"),
]
_CONTROL_RX = [(name, re.compile(rf"(?ix)^(?:{rx})$")) for name, rx in _CONTROLS]


def control(text: str) -> Optional[Intent]:
    s = _clean(text).lower()
    for name, rx in _CONTROL_RX:
        m = rx.match(s)
        if m:
            args = {}
            if name == "move":
                d = (m.groupdict().get("dir") or m.groupdict().get("dir2") or "").lower()
                args["dir"] = {"upar": "up", "neeche": "down"}.get(d, d)
            return Intent("control", name, args=args)
    return None


# --------------------------------------------------------------------------- drawing
_SHAPE = r"(?P<shape>triangle|circle|arrow|box|rectangle|square|line|trikon|gola|त्रिभुज|वृत्त|गोला|तीर)"


def draw(text: str) -> Optional[Intent]:
    s = _clean(text).lower()
    m = re.match(rf"(?ix)^(?:draw|make|put)\s+(?:me\s+)?(?:a|an|one)?\s*{_SHAPE}(?:\s+(?:here|on\s+(?:the\s+)?screen))?$", s)
    if not m:
        m = re.match(rf"(?ix)^(?:ek\s+|एक\s+)?{_SHAPE}\s+(?:banao|bana\s+do|draw\s+karo|बनाओ|बना\s+दो)$", s)
    if m:
        shape = next(v for k, v in m.groupdict().items() if v)
        shape = {"trikon": "triangle", "त्रिभुज": "triangle", "gola": "circle", "गोला": "circle", "वृत्त": "circle",
                 "तीर": "arrow", "box": "rectangle", "square": "rectangle"}.get(shape, shape)
        return Intent("draw", shape)
    m = re.match(r"(?ix)^(?:draw\s+)?(?:an?\s+)?arrow\s+from\s+(?P<a>this|that|here|there)\s+to\s+(?P<b>this|that|here|there)$", s)
    if m:
        return Intent("draw", "arrow_between", args={"from": m["a"], "to": m["b"]})
    if re.match(r"(?ix)^(?:highlight|mark)\s+(?:this|that|it)$|^(?:isko|ise)\s+highlight\s+karo$|^इसे\s+हाइलाइट\s+करो$", s):
        return Intent("draw", "highlight_this")
    if re.match(r"(?ix)^(?:circle|ring)\s+(?:this|that|it)$|^(?:isko|ise)\s+circle\s+karo$|^इसे\s+घेरो$", s):
        return Intent("draw", "circle_this")
    m = re.match(r"(?ix)^label\s+(?:this|that|it)\s+(?:as\s+)?(?P<label>.{1,40})$|^(?:isko|ise)\s+(?P<label2>.{1,40})\s+likho$", s)
    if m:
        return Intent("draw", "label_this", args={"label": (m["label"] or m["label2"]).strip(" \"'")})
    if re.match(r"(?ix)^(?:erase|delete|remove)\s+(?:that|this|it)$|^(?:isko|ise|woh)\s+(?:mitao|hatao)$|^(?:इसे|उसे)\s+(?:मिटाओ|हटाओ)$", s):
        return Intent("draw", "erase_that")
    return None


# --------------------------------------------------------------------------- lesson follow-ups
def follow_up(text: str, topic: str) -> Optional[Intent]:
    s = _clean(text).lower()
    if topic == "rag":
        if re.search(r"(?i)fine[\s-]*tun|compare|vs\.?|versus|फ़ाइन|फाइन|tulna|तुलना", s) and re.search(r"(?i)fine|फ़ाइन|फाइन|tun", s):
            return Intent("follow_up", "compare_finetune", "rag")
        if re.search(r"(?i)hallucinat|made[\s-]*up|makes?\s+(?:things|stuff)\s+up|भ्रम|मनगढ़ंत|hallucination\s+kahan", s):
            return Intent("follow_up", "hallucination", "rag")
        if re.search(r"(?i)(?:retriev\w*|search|context)\b.*\b(?:wrong|bad|incorrect|irrelevant|fails?|galat|garbage)|"
                     r"(?:wrong|bad|galat|ग़लत|गलत)\s+(?:data|chunks?|context|retrieval|results?)|ग़लत\s+chunks|गलत\s+टुकड़े|"
                     r"retrieval\s+(?:galat|ग़लत|गलत)", s):
            return Intent("follow_up", "bad_retrieval", "rag")
        if re.search(r"(?i)(?:where|show).*(?:chunk)|chunking|chunk\s+kahan|टुकड़े\s+कहाँ", s):
            return Intent("follow_up", "chunking", "rag")
        m = re.search(r"(?i)(?:make|karo)?.*\b(?:bigger|larger|bada)\b|बड़ा", s)
        node = rag.node_named(s)
        if m and node:
            return Intent("follow_up", "bigger", "rag", {"node": node})
        if node and re.search(r"(?i)explain|again|what\s+(?:is|does)|how\s+does|samjhao|phir|kya\s+(?:hai|karta)|समझाओ|क्या|दोबारा|tell\s+me\s+about", s):
            return Intent("follow_up", "explain", "rag", {"node": node})
    if topic == "pythagoras":
        if re.search(r"(?i)hypotenuse|कर्ण|longest|sabse\s+lambi|सबसे\s+लंबी", s):
            return Intent("follow_up", "hypotenuse", "pythagoras")
        if re.search(r"(?i)another\s+example|one\s+more\s+example|example\s+(?:do|dikhao|aur)|aur\s+example|और\s+उदाहरण|दूसरा\s+उदाहरण", s):
            return Intent("follow_up", "example", "pythagoras")
        from .lessons.pythagoras import answer_check
        if answer_check(s) is not None and re.search(r"(?i)^(?:it'?s\s+|c\s+(?:is|=)\s+|is\s+it\s+)?\S+(?:\s+\S+){0,4}$", s):
            return Intent("follow_up", "answer", "pythagoras", {"correct": answer_check(s)})
    return None
