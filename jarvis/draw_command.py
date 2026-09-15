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
        (?:\s+(?:on|in|onto)\s+(?:the\s+|my\s+)?(?P<where>whiteboard|canvas|screen|board|page))?
        (?:\s+please)?$""",
    re.IGNORECASE | re.VERBOSE,
)

# Left to the extraction, which knows what a thinned pen can carry. This module used to keep its
# own budget of 1400, which silently capped a twenty-thousand-point drawing at four strokes.
from .vision.strokes import DEFAULT_BUDGET as BUDGET


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
    if not subject or len(subject) < 3:
        return None
    if re.fullmatch(r"(?:it|that|this|something|anything)", subject, re.IGNORECASE):
        return None
    return subject


async def run(subject: str, config=None) -> str:
    from .integrations import browser
    from .vision import reference, strokes

    if not browser.control_ready():
        return ("I can only draw in a browser I can control — say “restart Opera with control” "
                "and open a whiteboard first.")

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
    return (f"Drew {subject} — {len(fitted)} strokes, {points:,} points, from a reference "
            f"picture. It's a line rendering, not a copy.")


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not a drawing request'."""
    subject = parse(text)
    if subject is None:
        return None
    try:
        return await run(subject, config)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't draw {subject} — {type(exc).__name__}."
