"""«make me a picture of X» — generated here, saved, and opened.

Kept separate from «draw X», which is a different request with a different answer: that one finds
a reference photograph and traces it onto an open whiteboard as pen strokes. This one produces an
image file. The verbs do not overlap — draw, sketch and paint go to the whiteboard; generate,
create, make, render and imagine come here — so neither can quietly take the other's requests.

Deterministic for the same reason every other common command is. "Make me a picture of a fox" has
exactly one meaning; handing it to a 3B model to decide costs seconds and loses attempts.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from typing import Optional

# The verb must be a making verb and the object must be an image. Either half alone is too loose:
# "generate a report" is not a picture, and "a picture of my screen" is a screenshot.
_MAKE = re.compile(
    r"""(?ix)^(?:please\s+|hey\s+)?
        (?:can\s+you\s+|could\s+you\s+|i\s+want\s+(?:you\s+to\s+)?|i\s+need\s+)?
        (?:generate|create|make|render|produce|design|imagine)\s+
        (?:me\s+)?(?:a\s+|an\s+|some\s+|the\s+)?
        (?:ai\s+)?
        # Adjectives sit between the article and the noun — "a detailed image", "a nice big
        # wallpaper" — and without room for them the request stops being recognised at all.
        (?:[\w-]+\s+){0,3}
        (?P<kind>image|picture|photo|painting|drawing|illustration|artwork|art|
                 wallpaper|logo|poster|portrait|render)s?\s+
        (?:of|showing|with|for|depicting)\s+
        (?P<subject>.+?)
        (?:\s+please)?$""",
)

# "an image of a fox" said without a verb at all, which is how people ask the second time.
_BARE = re.compile(
    r"""(?ix)^(?:i\s+(?:want|need)\s+|give\s+me\s+)?(?:an?\s+)?(?:ai\s+)?
        (?:[\w-]+\s+){0,2}
        (?P<kind>image|picture|wallpaper|logo|painting|illustration)\s+
        of\s+(?P<subject>.+?)$""",
)

# "imagine that", "imagine if" — reflection, not a request for a picture. _MAKE already requires an
# image noun after the verb, so these cannot reach it; this is here for the looser paths below.
_NOT_A_REQUEST = re.compile(r"(?ix)^\s*(?:imagine|picture)\s+(?:that|if|how|what|yourself|me\s+as)\b")

# Asking for detail buys steps. Measured: one step is 7.5s, four is 25.3s. Nobody should be made
# to wait twenty-five seconds without having asked for it, so detail is opt-in.
_WANTS_DETAIL = re.compile(r"(?ix)\b(?:detailed|high[- ]quality|hi[- ]res|high[- ]res|sharp|"
                           r"proper|really\s+good|best\s+quality|take\s+your\s+time)\b")

# Bigger is opt-in for the same reason: 768px is 14.2s against 512px at 7.5s.
_WANTS_BIG = re.compile(r"(?ix)\b(?:large|big|wallpaper|hd|768|full\s+size)\b")


def parse(text: str) -> Optional[str]:
    """What to make a picture of, or None when this is not that request."""
    cleaned = (text or "").strip().rstrip(".!?")
    if not cleaned or _NOT_A_REQUEST.match(cleaned):
        return None
    match = _MAKE.match(cleaned) or _BARE.match(cleaned)
    if not match:
        return None
    subject = match.group("subject").strip().strip("\"'")
    if not subject or len(subject) < 2:
        return None
    # "a picture of my screen" is a screenshot, and "a picture of the last one" needs context that
    # this handler does not have. Both belong to somebody else.
    if re.match(r"(?i)^(?:my|the|this|that)\s+(?:screen|display|desktop|monitor|window|tab)\b", subject):
        return None
    if re.fullmatch(r"(?i)(?:it|that|this|them|those|the\s+(?:last|first|other)\s+one)", subject):
        return None
    # The kind of picture asked for is part of the description — "a painting of a lighthouse"
    # should look like a painting — so it is carried into the prompt rather than discarded.
    kind = match.group("kind").lower()
    if kind in ("painting", "drawing", "illustration", "portrait", "poster", "logo", "art", "artwork"):
        subject = f"{subject}, {kind}"
    return subject


async def run(subject: str, said: str = "") -> str:
    """`said` is the whole sentence: the hints below live in words that parsing strips out.

    "Make a wallpaper of deep space" leaves the subject as "deep space" — the word that asked for
    a big picture is gone by then, so the hints are read from what was actually said.
    """
    from .vision import imagine

    hints = f"{said} {subject}"
    steps = 4 if _WANTS_DETAIL.search(hints) else imagine.DEFAULT_STEPS
    size = 768 if _WANTS_BIG.search(hints) else imagine.DEFAULT_SIZE

    try:
        made = await asyncio.to_thread(imagine.generate, subject, steps=steps, size=size)
    except imagine.Unavailable as exc:
        return f"I can't make pictures right now — {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't make that picture — {type(exc).__name__}: {exc}"

    # Showing it is most of the point. A failure to open is not a failure to make the picture, so
    # the path is reported either way and the user can go and look.
    try:
        subprocess.Popen(["xdg-open", str(made.path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass
    return f"Made it in {made.seconds} seconds, sir — saved to {made.path}."


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not a picture request'."""
    subject = parse(text)
    if subject is None:
        return None
    return await run(subject, text or "")
