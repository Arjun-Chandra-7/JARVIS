"""«draw me X» — find a picture of it, turn it into lines, and draw them where the pointer is."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

_DRAW = re.compile(
    r"""^(?:please\s+)?
        (?:draw|sketch|paint)\s+
        (?:me\s+)?(?:a\s+|an\s+|the\s+)?
        (?P<subject>.+?)
        (?:\s+(?:on|in|onto)\s+(?:the\s+|my\s+|a\s+|an\s+|some\s+)?
           (?P<where>whiteboard|canvas|screen|board|page)(?:\s+site)?)?
        (?:\s+please)?$""",
    re.IGNORECASE | re.VERBOSE,
)

# Left to the extraction, which knows what a thinned pen can carry. This module used to keep its
# own budget of 1400, which silently capped a twenty-thousand-point drawing at four strokes.
from .vision.strokes import DEFAULT_BUDGET as BUDGET


# The same pronouns find_site recognises, for the same reason: "draw this" is about the picture
# that was just made.
_POINTS_AT_A_PICTURE = re.compile(
    r"(?ix)^(?:this|it|that|them|the\s+(?:image|picture|photo|one)|"
    r"(?:this|that)\s+(?:image|picture|photo))$")


def parse(text: str) -> Optional[str]:
    match = _DRAW.match((text or "").strip().rstrip(".!?"))
    if not match:
        return None
    subject = match.group("subject").strip().strip("\"'")
    # "draw me a picture of a dog" is a request to draw a dog, and searching for the whole
    # phrase finds pictures of picture frames.
    subject = re.sub(r"^(?:a\s+|an\s+|the\s+)?(?:picture|image|photo|drawing|sketch)\s+of\s+"
                     r"(?:a\s+|an\s+|the\s+)?", "", subject, flags=re.IGNORECASE).strip()
    # "draw a line", "draw the curtains" — not requests for a picture of something.
    if not subject:
        return None
    # "Draw it" used to be rejected outright, because there was nothing for "it" to mean. Since
    # Jarvis can make a picture, there often is: "generate an image of Iron Man, then draw it on a
    # whiteboard" points at the picture from the first half. It is still rejected when no picture
    # has been made — a pronoun with nothing behind it is better handed to the model, which can
    # ask, than guessed at here.
    if _POINTS_AT_A_PICTURE.match(subject):
        from . import context
        return subject if context.last_picture() else None
    if len(subject) < 3:
        return None
    if re.fullmatch(r"(?:something|anything)", subject, re.IGNORECASE):
        return None
    return subject


async def run(subject: str, config=None) -> str:
    from .integrations import browser
    from .vision import reference, strokes

    if not browser.control_ready():
        return ("I can only draw in a browser I can control — say “restart the browser with "
                "control” and open a whiteboard first.")

    box = await browser.canvas_box()
    if not box:
        return ("There is no drawing surface on this page, sir. Open a whiteboard first — "
                "any page with a canvas will do.")

    # Thin the pen first. Density is bounded by stroke width, not by the extraction: at the
    # default width a detailed drawing fills its dark areas into a solid blob, because
    # neighbouring contours end up closer together than the line is wide.
    await browser.thin_pen()
    # And start from an empty board: these persist, so a second drawing lands on top of the first
    # and the pair of them reads as noise.
    await browser.clear_board()

    # "Draw this" after a picture was generated means that picture. Searching the web for a
    # reference to something Jarvis made itself a moment ago would find something else entirely.
    from . import context
    from pathlib import Path as _Path

    made = context.last_picture() if _POINTS_AT_A_PICTURE.match(subject.strip()) else ""
    if made and _Path(made).exists():
        picture, subject = _Path(made), "the picture you asked for"
    else:
        if _POINTS_AT_A_PICTURE.match(subject.strip()):
            return ("I don't have a picture to draw, sir — ask me to generate one first, or "
                    "name what to draw.")
        picture = await asyncio.to_thread(reference.find, subject)
        if picture is None:
            return f"I couldn't find a picture of {subject} to work from."

    plan = await asyncio.to_thread(strokes.from_image, picture, BUDGET)
    if plan is None:
        return f"I found a picture of {subject} but couldn't get any lines out of it."

    fitted = plan.scaled_into(box["x"], box["y"], box["w"], box["h"])
    drawn = await browser.draw_path(fitted)
    if not drawn.get("ok"):
        return drawn.get("error") or f"I couldn't draw {subject}."
    points = sum(len(stroke) for stroke in fitted)
    source = "the picture I generated" if made else "a reference picture"
    return (f"Drew {subject} — {len(fitted)} strokes, {points:,} points, from {source}. "
            f"It's a line rendering, not a copy.")


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not a drawing request'."""
    subject = parse(text)
    if subject is None:
        return None
    try:
        reply = await run(subject, config)
        # Recorded so "now Albert Einstein" can be rebuilt into this same request. Only on a
        # drawing that actually happened — a follow-up to a failure should not repeat the failure.
        if reply and reply.startswith("Drew "):
            from . import context
            context.note_action(text, subject)
        return reply
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't draw {subject} — {type(exc).__name__}."
