"""Questions about the video that is playing, answered from what was actually said in it.

    "Explain what he just said"          the last ~40 s of the transcript
    "Why is this step valid?"            same window, the question kept
    "Explain what is on screen"          transcript around now + chapter; a screenshot only if
                                         the video has no words to give
    "Pause and explain this part"        pause (checked on the player), then the window
    "Summarise the last two minutes"     the transcript from now-2:00 to now

Hindi and Hinglish work the same ("abhi kya bola, samjhao"), and the answer comes back in the
language the question was asked in. When the topic is schoolwork the explanation is pitched at a
CBSE Class 10 student.

The model is given the transcript excerpt and told to rely on it. When there is no transcript it
is told that too, so it says so rather than inventing what the teacher said.
"""
from __future__ import annotations

import os
import re
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
_WORDS = {"a": 1, "one": 1, "ek": 1, "two": 2, "do": 2, "three": 3, "teen": 3, "four": 4, "five": 5,
          "paanch": 5, "ten": 10}

_HINGLISH = re.compile(r"(?i)\b(?:kya|kyu|kyun|kaise|samjhao|samjha|bola|kaha|batao|abhi|ye|yeh|hai|"
                       r"ruko|karo|pichle|pichhle|minute|aur|ko|mein|kar)\b")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

_TUTOR = (
    "You are a patient tutor for a CBSE Class 10 student in India. You are given an excerpt of the "
    "transcript of the video they are watching, with timestamps, and their question. Base your "
    "answer on the excerpt; if the excerpt does not contain what is needed, say so plainly and "
    "then give the standard explanation, marked as your own. Explain step by step in short spoken "
    "sentences (this is read aloud: no markdown, no bullet symbols, write formulas in words such as "
    "'a squared plus b squared equals c squared'). Keep it under 120 words unless asked to "
    "summarise. Never claim the excerpt says something it does not.")


def intent(text: str) -> Optional[str]:
    said = (text or "").strip()
    if _PAUSE_EXPLAIN.search(said):
        return "pause_explain"
    if _SUMMARY.search(said):
        return "summary"
    if _JUST_SAID.search(said):
        return "just_said"
    if _ON_SCREEN.search(said):
        return "on_screen"
    return None


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
    return "Hinglish (Roman script)" if len(_HINGLISH.findall(text or "")) >= 2 else "English"


def build_prompt(kind: str, question: str, state: yt.PlayerState, excerpt: list[yt.Segment],
                 source: str, span: tuple[float, float]) -> str:
    lines = [f"Video: {state.title}" + (f" — {state.channel}" if state.channel else ""),
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
            "summary": "Summarise this stretch of the video in a few spoken sentences."}[kind]
    lines += ["", f"Task: {task}", f"The student asked: \"{question}\"",
              f"Answer in {language_of(question)}."]
    return "\n".join(lines)


async def handle(text: str, config=None) -> Optional[str]:
    kind = intent(text)
    if kind is None:
        return None
    from .screen.page import active_page, why_no_page

    page = active_page()
    if page is None:
        return why_no_page()
    player = yt.YouTube(page)
    try:
        state = await player.state()
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read the browser: {exc}"
    if not state.youtube:
        return None if kind == "on_screen" else "The page in front of you isn't a YouTube video."
    if state.time is None:
        return "I can see YouTube, but not a playing video on this page."
    if state.ad:
        return "An ad is playing. Ask me again when the video is back."
    if state.player_error:
        # Seen on automated or signed-out sessions: the player refuses to load at all.
        return (f'YouTube is showing an error instead of the video: "{state.player_error}". '
                "There's nothing playing for me to explain.")

    try:
        return await _answer(kind, text, state, player, config)
    except Exception as exc:  # noqa: BLE001 — a page that stops answering is said, not raised
        return f"The browser stopped answering while I was reading the video ({type(exc).__name__})."


async def _answer(kind: str, text: str, state: yt.PlayerState, player: yt.YouTube, config) -> str:
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
    elif kind == "on_screen":
        span = (max(0.0, now - 25), now + 10)
    else:
        span = (max(0.0, now - 40), now + 3)
    excerpt = yt.window(segments, span[1], before=span[1] - span[0], after=0)

    if not excerpt and not state.live_caption and kind == "on_screen":
        # No words to explain: this is what the screenshot is for — but only with a vision model
        # good enough to trust. The small local one describes a frame, it does not teach it.
        from .vision import analyze
        from .config import CONFIG
        if analyze.available(config or CONFIG) != "gemini" and \
                os.environ.get("JARVIS_ALLOW_WEAK_TEACHING", "") not in {"1", "true", "yes"}:
            return paused_note + ("This video has no captions, and the only vision model available is the "
                                  "small local one, which isn't reliable for explaining a frame.")
        from .integrations import lens
        import asyncio
        described = await asyncio.to_thread(lens.screen, f"Explain for a Class 10 student what this video frame "
                                                          f"from '{state.title}' shows: {text}")
        return paused_note + (described or "This video has no captions and I couldn't read the frame.")

    from .llm import complete_detailed
    done = await complete_detailed(_TUTOR, build_prompt(kind, text, state, excerpt, source, span), config,
                                   temperature=0.3, strength="strong")
    if done.ok:
        return paused_note + done.text
    # No strong model: say so, and give what the video actually said — the words need no model.
    said = " ".join(s.text for s in excerpt[-6:]) or state.live_caption
    heard = f" Here's what was said: {said}" if said else ""
    return paused_note + done.unavailable_message() + heard
