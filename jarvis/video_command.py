"""Questions about the video that is playing, answered from what was actually said in it.

    "Explain what he just said"          the last ~40 s of the transcript
    "Why is this step valid?"            same window, the question kept
    "Explain what is on screen"          transcript around now + chapter; a screenshot only if
                                         the video has no words to give
    "Pause and explain this part"        pause (checked on the player), then the window
    "Summarise the last two minutes"     the transcript from now-2:00 to now

Hindi and Hinglish work the same ("abhi kya bola, samjhao", "अब क्या बोला, यह समझाना?"), and the
answer comes back in the language the question was asked in. When the topic is schoolwork the
explanation is pitched at a CBSE Class 10 student.

The model is given the transcript excerpt and told to rely on it. When there is no transcript
the model is not asked at all: it used to say "the transcript isn't available" and then describe
what the video "likely" covered, from the title — an invented lesson in a teacher's voice.

"Jarvis, अब क्या बोला, यह समझाना?" matched nothing here, because every pattern was in Latin
letters. It went to the general model with its memory tool, which answered from an older
conversation about the terminal and CPU usage. A question about what is on screen now is answered
from the screen or not at all: ``is_current_context`` is checked again after every handler, so it
can never fall through to memory.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from .screen import youtube as yt

_JUST_SAID = re.compile(
    r"(?ix)\b(?:what\s+(?:did\s+)?(?:he|she|they|the\s+(?:teacher|speaker|guy))\s+(?:just\s+)?(?:said|say|meant?)|"
    r"(?:explain|repeat)\s+what\s+(?:he|she|they|was)\s+(?:just\s+)?(?:said|say|explained)|"
    r"just\s+said|abhi\s+(?:kya\s+)?(?:bola|kaha|bataya|samjhaya)|kya\s+bola|kya\s+kaha|"
    r"why\s+is\s+(?:this|that)\s+step|how\s+did\s+(?:he|she|they)\s+get|ye\s+step\s+kaise|ye\s+kaise\s+aaya)")
_ON_SCREEN = re.compile(
    r"(?ix)\b(?:(?:explain|what(?:'s|\s+is))\s+(?:what(?:'s|\s+is)\s+)?(?:currently\s+)?on\s+(?:the\s+)?screen|"
    r"(?:explain|what(?:'s|\s+is))\s+(?:this|the)\s+(?:equation|formula|diagram|slide|graph)|"
    r"screen\s+(?:pe|par)\s+kya|ye\s+kya\s+(?:dikha|likha)|is\s+(?:equation|formula)\s+ko\s+samjhao)")
_PAUSE_EXPLAIN = re.compile(
    r"(?ix)\b(?:pause\s+(?:it\s+|the\s+video\s+|this\s+)?and\s+(?:explain|teach)|"
    r"(?:ruko|rok\s+do|pause\s+karo|pause\s+karke)\s+(?:aur\s+)?(?:samjhao|explain|batao)|"
    r"pause\s+and\s+teach\s+me)")
_SUMMARY = re.compile(
    r"(?ix)\b(?:summari[sz]e|summary\s+of|recap)\s+(?:the\s+)?(?:last|past|previous)\s+"
    r"(?P<n>\d+|a|one|two|three|four|five|ten)\s+(?P<unit>minutes?|mins?|seconds?)|"
    r"pichh?le\s+(?P<hn>\d+|do|teen|ek|paanch)\s+minute")

# The same questions in Devanagari, as Whisper writes them when it hears Hindi. No \b here:
# Python treats the vowel signs as non-word characters, so word boundaries fall mid-word.
# समजाना is how the recogniser spells समझाना often enough to list.
_EXPLAIN_HI = r"(?:समझाओ|समझाना|समजाना|समजाओ|समझाइए|समझा\s*दो|समझा|समजा|explain|बताओ|बताना)"
_PART_HI = r"(?:part|step|पार्ट|स्टेप|हिस्सा|हिस्से|line|लाइन)"
_JUST_SAID_HI = re.compile(
    r"(?i)(?:(?:अब|अभी)\s+(?:जो\s+|ये\s+|यह\s+)?(?:क्या\s+)?(?:बोला|बोले|बोली|कहा|बताया|समझाया|समजाया)"
    r"|क्या\s+(?:बोला|बोले|कहा)"
    r"|(?:ये|यह|वो)\s+क्या\s+(?:समझा|समजा|बता|पढ़ा)\s*(?:रहा|रहे|रही)"
    rf"|(?:ये|यह|इस|अभी|अब)\s+(?:वाला\s+|वाले\s+|वाली\s+)?{_PART_HI}\s*(?:को\s+)?(?:{_EXPLAIN_HI}|क्या\s+था)"
    rf"|(?:अभी|अब)\s+(?:वाला|वाले)\s+{_PART_HI})")
_ON_SCREEN_HI = re.compile(r"(?:स्क्रीन|screen)\s+(?:पे|पर)\s+क्या")
_PAUSE_HI = re.compile(r"(?:रुको|रोको|pause\s+करो|pause\s+करके)\s+(?:और\s+)?(?:समझाओ|समझा|बताओ|explain)")

# The Latin-script forms the English patterns above do not cover.
_JUST_SAID_MORE = re.compile(
    r"(?ix)\b(?:(?:ab|abhi)\s+(?:kya\s+)?(?:bola|bole|kaha|bataya|samjhaya)|"
    r"(?:ye|yeh|woh?)\s+kya\s+(?:samjha|bata|padha)\s*(?:raha|rahe|rahi)|"
    r"(?:ye|yeh|is|abhi|ab)\s+(?:wala\s+|wale\s+|wali\s+)?(?:part|step|hissa|line)\s+"
    r"(?:ko\s+)?(?:samjhao|samjha\s+do|samjhana|samjha|explain|kya\s+tha|batao)|"
    r"(?:explain|repeat)\s+(?:this|that|the\s+last)\s+(?:part|step|bit|section|line)|"
    r"what\s+(?:did|was)\s+(?:he|she|they)\s+just\s+(?:say|said|explain)|"
    r"what\s+(?:is|was)\s+(?:he|she|they)\s+(?:just\s+)?(?:explaining|saying)(?:\s+(?:now|right\s+now))?)")

# The whole video rather than the last few seconds: "tell me what is happening in the video",
# "what video am I looking at and give me a summary". Both were said live and matched nothing,
# so the model answered "I can't see videos".
_OVERVIEW = re.compile(
    r"(?ix)\b(?:what(?:'s|\s+is)\s+(?:happening|going\s+on)\s+in\s+(?:the|this|my)\s+video|"
    r"what\s+(?:video|is\s+this\s+video)\s+(?:am\s+i|are\s+we|is\s+this)?\s*(?:watching|looking\s+at|playing|on)|"
    r"what\s+video\s+(?:is\s+)?(?:this|playing|on\s+(?:my|the)\s+screen)|"
    r"what(?:'s|\s+is)\s+(?:this|the)\s+video\s+about|"
    r"(?:summari[sz]e|recap|explain|describe)\s+(?:this|the|my|that)\s+video|"
    r"(?:give\s+me\s+)?(?:a\s+)?summary\s+of\s+(?:this|the|that)\s+video|"
    r"(?:the\s+)?video\s+on\s+(?:my|the)\s+screen|"
    r"(?:is|ye|yeh|iss?)\s+video\s+(?:mein|me|mai)\s+kya|video\s+(?:mein|me)\s+kya\s+(?:ho|chal)\s+raha|"
    r"(?:ye|yeh)\s+video\s+(?:kis|kiss)\s+(?:baare|bare)|video\s+ka\s+summary)")
_OVERVIEW_HI = re.compile(
    r"(?:वीडियो|video)\s+(?:में|मे)\s+क्या|(?:वीडियो|video)\s+(?:किस|किसके)\s+बारे|"
    r"(?:वीडियो|video)\s+का\s+(?:summary|सारांश)")

# Asked about the past on purpose: "what did he say yesterday", "kal wale video mein kya bola".
# Only then is this a question for memory rather than for the screen.
_EXPLICIT_PAST = re.compile(
    r"(?ix)\b(?:yesterday|last\s+(?:week|night|time|session)|earlier\s+today|the\s+other\s+day|"
    r"kal|pichli\s+baar|parso)\b|कल|पिछली\s+बार|परसों")

_PURE_HINDI = re.compile(r"(?i)\b(?:pure|shuddh|only|sirf|keval)\s+hindi\b|शुद्ध\s+हिंदी|सिर्फ़?\s+हिंदी|केवल\s+हिंदी")
_ASKS_ENGLISH = re.compile(r"(?i)\b(?:in\s+english|english\s+(?:mein|me|mai))\b|अंग्रेज़?ी\s+में")

_WORDS = {"a": 1, "one": 1, "ek": 1, "two": 2, "do": 2, "three": 3, "teen": 3, "four": 4, "five": 5,
          "paanch": 5, "ten": 10}

_HINGLISH = re.compile(r"(?i)\b(?:kya|kyu|kyun|kaise|samjhao|samjha|bola|kaha|batao|abhi|ye|yeh|hai|"
                       r"ruko|karo|pichle|pichhle|minute|aur|ko|mein|kar|tha|wala|isko|ise|iska|iski|"
                       r"isme|ismein|matlab|bhai|yaar)\b")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

_TUTOR = (
    "You are a patient tutor for a CBSE Class 10 student in India. You are given an excerpt of the "
    "transcript of the video they are watching, with timestamps, and their question. Base your "
    "answer only on the excerpt. If the excerpt does not contain what is needed, say so plainly "
    "and stop: never guess what the teacher said from the title or chapter. Explain step by step, "
    "conversationally, in short spoken sentences (this is read aloud: no markdown, no bullet "
    "symbols, write formulas in words such as 'a squared plus b squared equals c squared'). Keep "
    "it under 120 words unless asked to summarise. Never claim the excerpt says something it does "
    "not.")


def intent(text: str) -> Optional[str]:
    said = (text or "").strip()
    if _EXPLICIT_PAST.search(said):
        return None                      # a question about the past is memory's, not the screen's
    if _PAUSE_EXPLAIN.search(said) or _PAUSE_HI.search(said):
        return "pause_explain"
    if _SUMMARY.search(said):
        return "summary"
    if _JUST_SAID.search(said) or _JUST_SAID_MORE.search(said) or _JUST_SAID_HI.search(said):
        return "just_said"
    if _OVERVIEW.search(said) or _OVERVIEW_HI.search(said):
        return "overview"
    if _ON_SCREEN.search(said) or _ON_SCREEN_HI.search(said):
        return "on_screen"
    if _PAGE_EN.match(said.rstrip(" .?!")) or _PAGE_HINGLISH.search(said) or _PAGE_HI.search(said):
        return "page"
    return None


# "Explain this", "summarise this page", "what am I looking at", "ye kya hai", "इसे समझाओ" — about
# whatever is in front of them, on any page or in any app. The short English forms are matched as
# the whole sentence, so "what is this theorem called" stays a question for the model.
_THING = (r"(?:page|article|post|site|website|screen|document|doc|pdf|email|mail|thread|chat|"
          r"conversation|code|error|paragraph|section|question|problem|slide|diagram|chart|graph|table|"
          r"tweet|repo|readme|message|notice|essay|chapter|text|passage|answer|solution|formula)")
_PAGE_EN = re.compile(
    rf"""(?ix)^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:
        what\s+am\s+i\s+(?:looking\s+at|reading|seeing)|
        what(?:'s|\s+is)\s+(?:this|that|going\s+on\s+here|happening\s+here|happening\s+on\s+(?:my|the)\s+screen)(?:\s+about)?|
        what(?:'s|\s+is)\s+(?:this|the)\s+{_THING}\s+about|
        (?:explain|summari[sz]e|recap|describe|simplify|break\s+down|tl;?dr|read\s+and\s+explain)\s+
            (?:this|that|it|what\s+i'?m\s+(?:looking\s+at|reading)|(?:this|the|my)\s+(?:\w+\s+)?{_THING})(?:\s+.*)?|
        what\s+does\s+(?:this|that)\s+mean|
        help\s+me\s+understand\s+(?:this|that|it)(?:\s+.*)?|
        (?:give\s+me\s+)?(?:a\s+)?(?:summary|tl;?dr|gist)\s+of\s+(?:this|the)\s+(?:\w+\s+)?{_THING}
    )$""")
_PAGE_HINGLISH = re.compile(
    rf"""(?ix)(?:^(?:ye|yeh|yah)\s+kya\s+(?:hai|h)\b|
        \b(?:ye|yeh|isko|ise|is\s+{_THING}(?:\s+ko)?)\s+(?:samjhao|samjha\s+do|samjhana|explain\s+karo|
            summari[sz]e\s+karo)|
        \b(?:iska|iski|is\s+{_THING}\s+ka)\s+(?:summary|saar|matlab|gist)|
        \b(?:ye|yeh|is)\s+{_THING}\s+(?:kis|kiss)\s+(?:baare|bare)|
        \bscreen\s+(?:pe|par)\s+kya\s+(?:hai|chal\s+raha))""")
_PAGE_HI = re.compile(
    r"(?:^(?:ये|यह)\s+क्या\s+है"
    r"|(?:ये|यह|इसे|इसको|इस\s+(?:page|पेज|article|code|कोड|error|question|सवाल|screen|स्क्रीन|"
    r"email|chapter|पाठ)(?:\s+को)?)\s*(?:समझाओ|समझा\s*दो|समझाना|समजाओ|समजाना|explain\s+करो)"
    r"|(?:इसका|इसकी|इस\s+\S+\s+का)\s+(?:summary|सारांश|मतलब)"
    r"|(?:स्क्रीन|screen)\s+(?:पे|पर)\s+क्या)")


def is_current_context(text: str) -> bool:
    """A question about what is on screen or playing right now."""
    return intent(text) is not None


def summary_seconds(text: str) -> float:
    m = _SUMMARY.search(text or "")
    if not m:
        return 120.0
    raw = (m.group("n") or m.group("hn") or "2").lower()
    amount = float(raw) if raw.isdigit() else float(_WORDS.get(raw, 2))
    unit = (m.group("unit") or "minutes").lower()
    return amount if unit.startswith("s") else amount * 60


def language_of(text: str) -> str:
    if _DEVANAGARI.search(text or ""):
        return "Hindi"
    # "do"/"de" end a Hindi request ("iska summary do") but are everyday English mid-sentence.
    hits = len(_HINGLISH.findall(text or "")) + bool(re.search(r"(?i)\b(?:do|de|dena)\s*[.?!]*$", text or ""))
    return "Hinglish (Roman script)" if hits >= 2 else "English"


def reply_language(text: str) -> str:
    """en, hinglish (Roman), hi (Hinglish in Devanagari) or hi-pure. An explicit request wins."""
    said = text or ""
    if _PURE_HINDI.search(said):
        return "hi-pure"
    if _ASKS_ENGLISH.search(said):
        return "en"
    if _DEVANAGARI.search(said):
        return "hi"
    if language_of(said).startswith("Hinglish") or re.search(r"(?i)\bhinglish\b", said):
        return "hinglish"
    return "en"


_LANGUAGE_INSTRUCTION = {
    "en": "Answer in English.",
    "hinglish": ("Answer in Hinglish (Roman script), the way friends talk: Hindi sentence structure, "
                 "English words for the technical terms."),
    "hi": ("Answer in Hinglish written in Devanagari, the way an Indian teacher actually talks: "
           "natural conversational Hindi with the technical terms kept in English (for example "
           "\"Teacher अभी बता रहा था कि right-angled triangle में hypotenuse का square…\"). "
           "Not formal or translated Hindi."),
    "hi-pure": "Answer in pure, natural Hindi in Devanagari, as the student asked. Keep it conversational.",
}

# What to say when there is nothing honest to explain, in the language the question came in.
_SAY = {
    "no_transcript": {
        "en": "I can't get this video's transcript right now. Turn captions on and ask me again.",
        "hinglish": "Mujhe abhi video ka transcript nahi mil raha. Captions on karke phir bolo.",
        "hi": "मुझे अभी वीडियो का transcript नहीं मिल रहा। Captions on करके फिर बोलो।"},
    "not_youtube": {
        "en": "The page in front of you isn't a YouTube video.",
        "hinglish": "Saamne wala tab YouTube video nahi hai, isliye main nahi bata sakta ki abhi kya bola gaya.",
        "hi": "सामने वाला tab YouTube video नहीं है, इसलिए मैं नहीं बता सकता कि अभी क्या बोला गया।"},
    "no_video": {
        "en": "I can see YouTube, but not a playing video on this page.",
        "hinglish": "YouTube khula hai, par is page pe koi video nahi chal raha.",
        "hi": "YouTube खुला है, पर इस page पे कोई video नहीं चल रहा।"},
    "stale": {
        "en": "The video just changed and the player hasn't caught up yet. Ask me again in a second.",
        "hinglish": "Video abhi badla hai, player ne update nahi kiya. Ek second mein phir poocho.",
        "hi": "Video अभी बदला है, player ने update नहीं किया। एक second में फिर पूछो।"},
    "ad": {
        "en": "An ad is playing. Ask me again when the video is back.",
        "hinglish": "Abhi ad chal raha hai. Video wapas aaye toh phir poocho.",
        "hi": "अभी ad चल रहा है। Video वापस आए तो फिर पूछो।"},
    "no_browser": {
        "hinglish": 'Main abhi browser nahi padh pa raha. "Restart the browser with control" bolo, phir dobara poocho.',
        "hi": 'मैं अभी browser नहीं पढ़ पा रहा। "Restart the browser with control" बोलो, फिर दोबारा पूछो।'},
    "no_screen": {
        "en": "I can't see what's playing right now, so I won't guess. Open the video in the browser and ask again.",
        "hinglish": ("Mujhe abhi screen pe kya chal raha hai woh nahi dikh raha, toh main guess nahi "
                     "karunga. Video browser mein kholo aur phir poocho."),
        "hi": "मुझे अभी screen पे क्या चल रहा है वो नहीं दिख रहा, तो मैं guess नहीं करूँगा। Video browser में खोलो और फिर पूछो।"},
    "browser_stopped": {
        "en": "The browser stopped answering while I was reading the video.",
        "hinglish": "Video padhte waqt browser ne jawab dena band kar diya.",
        "hi": "Video पढ़ते वक़्त browser ने जवाब देना बंद कर दिया।"},
}


def say(key: str, lang: str) -> str:
    table = _SAY[key]
    return table.get("hi" if lang == "hi-pure" else lang) or table.get("en", "")


def unavailable(text: str) -> str:
    """For the router: a current-screen question that nothing could answer from the screen."""
    return say("no_screen", reply_language(text))


def _log(kind: str, lang: str, context: str, action: str, state: Optional[yt.PlayerState] = None,
         span: Optional[tuple[float, float]] = None) -> None:
    from . import route_log
    fields = {"intent": f"screen.{kind}", "lang": lang, "context": context, "action": action,
              "memory": "not_consulted"}
    if state is not None:
        fields.update(video_id=state.video_id, title=(state.title or "")[:60],
                      position=yt.clock(state.time), fresh=state.fresh,
                      age_s=round(time.time() - state.read_at, 1) if state.read_at else None)
    if span is not None:
        fields["window"] = f"{yt.clock(span[0])}-{yt.clock(span[1])}"
    route_log.record(**fields)


_EXCERPT_CHARS = 6000


def _fit(excerpt: list, limit: int = _EXCERPT_CHARS) -> list:
    """Ten minutes of transcript is about the whole per-minute allowance of the strong model on
    its free tier; sent twice in a row, the second question was answered by nobody. A long
    stretch is thinned evenly, so a summary still covers all of it."""
    total = sum(len(s.text) + 8 for s in excerpt)
    if total <= limit:
        return excerpt
    keep = max(1, int(len(excerpt) * limit / total))
    step = len(excerpt) / keep
    picked = [excerpt[int(i * step)] for i in range(keep)]
    if picked[-1] is not excerpt[-1]:
        picked[-1] = excerpt[-1]          # the most recent line is the one "now" refers to
    return picked


def build_prompt(kind: str, question: str, state: yt.PlayerState, excerpt: list[yt.Segment],
                 source: str, span: tuple[float, float]) -> str:
    lines = [f"Video: {state.title}" + (f" — {state.channel}" if state.channel else "")
             + (f" [id {state.video_id}, read from the open tab just now]" if state.video_id else ""),
             f"Playback position: {yt.clock(state.time)}" + (f" of {yt.clock(state.duration)}" if state.duration else "")]
    if state.chapter:
        lines.append(f"Current chapter: {state.chapter}")
    if excerpt:
        lines.append(f"Transcript from {yt.clock(span[0])} to {yt.clock(span[1])} (from {source}):")
        lines.append(yt.as_text(excerpt))
    elif state.live_caption:
        lines.append(f"Only the caption currently on screen is available: \"{state.live_caption}\"")
    else:
        lines.append("No transcript or captions are available for this video.")
    task = {"just_said": "Explain what was just said, in the last part of the excerpt.",
            "pause_explain": "The video is paused here. Teach this part.",
            "on_screen": "Explain what is being shown and discussed right now.",
            "summary": "Summarise this stretch of the video in a few spoken sentences.",
            "overview": ("Say in one line which video this is, then summarise what the excerpt covers "
                         "in a few spoken sentences.")}[kind]
    lines += ["", f"Task: {task}", f"The student asked: \"{question}\"",
              _LANGUAGE_INSTRUCTION[reply_language(question)]]
    return "\n".join(lines)


async def handle(text: str, config=None) -> Optional[str]:
    kind = intent(text)
    if kind is None:
        return None
    lang = reply_language(text)
    from . import screen_context as sc
    from .screen import page as _pages
    from .screen.page import why_no_page

    # Where the person is looking: the tab in front, or the application in front.
    try:
        ctx = await sc.locate(prefer_video=kind != "page")
    except Exception:  # noqa: BLE001 — fall back to the older ways of finding a page
        ctx = sc.ScreenContext()
    if ctx.source == "app":
        return await _explain_app(kind, text, ctx, config, lang)
    page = ctx.page or _pages.video_page()
    if page is None:
        # No browser to drive: read the screen itself before giving up.
        await sc.fill_from_app(ctx)
        if ctx.source == "app":
            return await _explain_app(kind, text, ctx, config, lang)
        _log(kind, lang, "none", "no_browser")
        return why_no_page() if lang == "en" else say("no_browser", lang)
    player = yt.YouTube(page)
    try:
        state = await player.state()
    except Exception as exc:  # noqa: BLE001
        _log(kind, lang, "none", "browser_error")
        return f"I couldn't read the browser: {exc}" if lang == "en" else say("browser_stopped", lang)
    if not state.youtube:
        try:
            return await _explain_page(kind, text, page, config, lang)
        except Exception as exc:  # noqa: BLE001 — a page that stops answering is said, not raised
            _log(kind, lang, "none", "browser_error")
            return (f"The browser stopped answering while I was reading the page ({type(exc).__name__})."
                    if lang == "en" else say("browser_stopped", lang))
    if kind == "page":
        kind = "overview"                 # "explain this" on a video page is about the video
    if state.time is None:
        _log(kind, lang, "youtube", "no_video", state)
        return say("no_video", lang)
    if not state.fresh:
        # The address bar has moved on and the player still describes the last video: its title
        # and captions would explain the wrong thing, confidently.
        _log(kind, lang, "stale", "refused", state)
        return say("stale", lang)
    if state.ad:
        _log(kind, lang, "youtube", "ad", state)
        return say("ad", lang)
    if state.player_error:
        # Seen on automated or signed-out sessions: the player refuses to load at all.
        _log(kind, lang, "youtube", "player_error", state)
        return (f'YouTube is showing an error instead of the video: "{state.player_error}". '
                "There's nothing playing for me to explain.")

    try:
        return await _answer(kind, text, state, player, config, lang)
    except Exception as exc:  # noqa: BLE001 — a page that stops answering is said, not raised
        _log(kind, lang, "none", "browser_error", state)
        if lang == "en":
            return f"The browser stopped answering while I was reading the video ({type(exc).__name__})."
        return say("browser_stopped", lang)


async def _answer(kind: str, text: str, state: yt.PlayerState, player: yt.YouTube, config,
                  lang: str = "en") -> str:
    paused_note = ""
    if kind == "pause_explain":
        done = await player.control("pause")
        if not done.get("verified"):
            return f"I couldn't pause the video ({done.get('reason') or 'it kept playing'})."
        paused_note = "Paused. "
        state.time = done.get("time", state.time)

    segments, source = await player.transcript(state)
    now = float(state.time or 0)
    if kind == "summary":
        span = (max(0.0, now - summary_seconds(text)), now)
    elif kind == "overview":
        # What has been watched, up to the last twenty minutes of it; a video not started yet
        # is summarised from its opening.
        span = (max(0.0, now - 1200), now + 5) if now >= 60 else (0.0, 600.0)
    elif kind == "on_screen":
        span = (max(0.0, now - 25), now + 10)
    else:
        span = (max(0.0, now - 40), now + 3)
    excerpt = _fit(yt.window(segments, span[1], before=span[1] - span[0], after=0))

    if not excerpt and not state.live_caption and kind == "on_screen":
        # No words to explain: this is what the screenshot is for — but only with a vision model
        # good enough to trust. The small local one describes a frame, it does not teach it.
        from .vision import analyze
        from .config import CONFIG
        if analyze.available(config or CONFIG) != "gemini" and \
                os.environ.get("JARVIS_ALLOW_WEAK_TEACHING", "") not in {"1", "true", "yes"}:
            _log(kind, lang, "none", "no_vision", state, span)
            return paused_note + ("This video has no captions, and the only vision model available is the "
                                  "small local one, which isn't reliable for explaining a frame.")
        from .integrations import lens
        import asyncio
        _log(kind, lang, "vision", "explain", state, span)
        described = await asyncio.to_thread(lens.screen, f"Explain for a Class 10 student what this video frame "
                                                          f"from '{state.title}' shows: {text}")
        return paused_note + (described or "This video has no captions and I couldn't read the frame.")

    if not excerpt and not state.live_caption:
        # Nothing the teacher said is available. Asked anyway, the model filled the gap from the
        # title — "the video likely introduces…" — which is exactly what must not happen.
        _log(kind, lang, "none", "no_transcript", state, span)
        return paused_note + say("no_transcript", lang)

    context = "youtube_transcript" if excerpt else "live_caption"
    _log(kind, lang, context, "explain", state, span)
    from .llm import complete_detailed
    done = await complete_detailed(_TUTOR, build_prompt(kind, text, state, excerpt, source, span), config,
                                   temperature=0.3, strength="strong")
    if done.ok:
        return paused_note + done.text
    # No strong model: say so, and give what the video actually said — the words need no model.
    said = " ".join(s.text for s in excerpt[-6:]) or state.live_caption
    heard = f" Here's what was said: {said}" if said else ""
    return paused_note + done.unavailable_message() + heard


# =========================================================================== anywhere else
#
# The same questions on any page and in any app. A page with a <video> is asked about through
# that video's own caption track; everything else through what the person selected, what is in
# view, or the whole text — and outside the browser, the app's accessibility text and OCR.

_EXPLAINER = (
    "You explain what a person is looking at on their screen right now. You are given what was "
    "read from it — the window or page title, possibly the text they selected, the text in view, "
    "the page's text, a video's captions, or an OCR reading of an application window (which may "
    "contain reading errors). Answer only from that material. If it does not contain what is "
    "needed, say so plainly and stop; never guess or fill in from general knowledge as if it were "
    "on the screen. If it is schoolwork, pitch it at a CBSE Class 10 student. This is read aloud: "
    "short spoken sentences, no markdown or bullet symbols, formulas in words. Under 120 words "
    "unless asked to summarise.")

_SUMMARY_WORDS = re.compile(r"(?i)summar|recap|tl;?dr|overview|gist|saar|सारांश|kis\s+baare|किस\s+बारे|\babout\b")

_SAY.update({
    "no_page_video": {
        "en": "There's no video on this page, so nothing was said for me to explain.",
        "hinglish": "Is page pe koi video nahi hai, toh batane ke liye kuch bola hi nahi gaya.",
        "hi": "इस page पे कोई video नहीं है, तो बताने के लिए कुछ बोला ही नहीं गया।"},
    "no_captions": {
        "en": "This video has no captions I can read, so I can't tell what was said. Turn captions on and ask again.",
        "hinglish": ("Is video ke captions mujhe nahi mil rahe, toh main nahi bata sakta kya bola gaya. "
                     "Captions on karke phir poocho."),
        "hi": "इस video के captions मुझे नहीं मिल रहे, तो मैं नहीं बता सकता क्या बोला गया। Captions on करके फिर पूछो।"},
    "nothing_readable": {
        "en": "I can't read anything in the window in front of you, so I won't guess.",
        "hinglish": "Saamne wali window mein mujhe kuch padhne ko nahi mila, toh main guess nahi karunga.",
        "hi": "सामने वाली window में मुझे कुछ पढ़ने को नहीं मिला, तो मैं guess नहीं करूँगा।"},
    "no_model": {
        "en": "No strong model is reachable right now, so I can't explain it properly.",
        "hinglish": "Abhi koi strong model available nahi hai, toh main theek se samjha nahi paunga.",
        "hi": "अभी कोई strong model available नहीं है, तो मैं ठीक से समझा नहीं पाऊँगा।"},
})

_VIDEO_KINDS = {"just_said", "pause_explain", "summary", "overview"}


def _clip(text: str, limit: int = _EXCERPT_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _wants_summary(kind: str, text: str) -> bool:
    return kind in {"summary", "overview"} or bool(_SUMMARY_WORDS.search(text or ""))


def _material(kind: str, text: str, selection: str, in_view: str, body: str) -> tuple[str, str]:
    """(which text, what it is) for this question. A selection is always the "this"."""
    if selection:
        return selection, "the text they selected"
    if _wants_summary(kind, text):
        return body or in_view, "the page's text"
    return in_view or body, "the text in view"


async def _ask(text: str, header: list[str], task: str, lang: str, config) -> tuple[bool, str]:
    from .llm import complete_detailed
    prompt = "\n".join(header + ["", f"Task: {task}", f"They asked: \"{text}\"", _LANGUAGE_INSTRUCTION[lang]])
    done = await complete_detailed(_EXPLAINER, prompt, config, temperature=0.3, strength="strong")
    return done.ok, done.text


async def _explain_page(kind: str, text: str, page, config, lang: str) -> str:
    from . import screen_context as sc

    around = 1200.0 if kind == "overview" else summary_seconds(text) if kind == "summary" else 60.0
    got = await sc.read_page(page, around_s=around)
    title, url, video = got.get("title", ""), got.get("url", ""), got.get("video") or {}
    host = re.sub(r"^https?://(?:www\.)?([^/]+).*$", r"\1", url or "")
    header = [f"Page: {title}" + (f" ({host})" if host else "")]

    if kind in _VIDEO_KINDS and kind != "overview" and not video:
        _log(kind, lang, "page_no_video", "refused")
        return say("no_page_video", lang)
    if video and kind in _VIDEO_KINDS:
        paused = ""
        if kind == "pause_explain":
            done = await yt.YouTube(page).control("pause")
            if not done.get("verified"):
                return f"I couldn't pause the video ({done.get('reason') or 'it kept playing'})."
            paused = "Paused. "
        now = float(video.get("time") or 0)
        lo = now - (around if kind in {"summary", "overview"} else 40)
        lines = [(s, t) for s, t in video.get("captions") or [] if lo <= s <= now + 3 and t]
        if lines:
            excerpt = _fit([yt.Segment(float(s), 0.0, t) for s, t in lines])
            header += [f"Video position: {yt.clock(now)}"
                       + (f" of {yt.clock(video.get('duration'))}" if video.get("duration") else ""),
                       f"Captions from {yt.clock(excerpt[0].start)} to {yt.clock(now)}:", yt.as_text(excerpt)]
            _log(kind, lang, "page_captions", "explain")
            task = {"just_said": "Explain what was just said, in the last part of the captions.",
                    "pause_explain": "The video is paused here. Teach this part.",
                    "summary": "Summarise this stretch of the video.",
                    "overview": "Say what this video is and summarise what the captions cover."}[kind]
            ok, answer = await _ask(text, header, task, lang, config)
            return paused + (answer if ok else f"{say('no_model', lang)} {_clip(excerpt[-1].text, 300)}")
        if kind != "overview":
            _log(kind, lang, "page_video_no_captions", "refused")
            return paused + say("no_captions", lang)
        # "What is this video?" without captions: the page around it can still say what it is.

    material, what = _material(kind, text, got.get("selection", ""), got.get("in_view", ""), got.get("body", ""))
    if not material.strip():
        _log(kind, lang, "page_empty", "refused")
        return say("nothing_readable", lang)
    header += [f"From {what}:", _clip(material)]
    context = {"the text they selected": "page_selection", "the page's text": "page_text"}.get(what, "page_view")
    _log(kind, lang, context, "explain")
    task = ("Summarise this in a few spoken sentences." if _wants_summary(kind, text)
            else "Explain what this is and what it means, answering their question.")
    ok, answer = await _ask(text, header, task, lang, config)
    return answer if ok else say("no_model", lang)


async def _explain_app(kind: str, text: str, ctx, config, lang: str) -> str:
    """Outside the browser: the app's accessibility text, else OCR. A video playing in an app
    has no caption track to reach, so only what is written on screen can be explained."""
    material = ctx.body if _wants_summary(kind, text) else ctx.in_view
    if not (material or "").strip():
        _log(kind, lang, "app_empty", "refused")
        return say("nothing_readable", lang)
    reading = ("its accessibility text" if ctx.note == "atspi"
               else "an OCR reading of the screen (may contain reading errors)")
    header = [f"Window: {ctx.title or ctx.window or 'unknown'}" + (f" (app: {ctx.app})" if ctx.app else ""),
              f"From {reading}:", _clip(material)]
    _log(kind, lang, f"app_{ctx.note or 'text'}", "explain")
    ok, answer = await _ask(text, header, "Explain what is on their screen, answering their question.",
                            lang, config)
    return answer if ok else say("no_model", lang)
