"""«what does my screen say» — read it, do not ask a model to look at it.

Wiring OCR into the lens was not enough on its own: a spoken question still went to the model
first, which chose a screenshot tool and then described the picture. Measured, that answered "It's
a screenshot of your current desktop. Click on something specific if you want me to describe it"
after twenty-three seconds — slower than reading every word on the screen and less use than any
of them.

So the question is answered here. "What does it say" is about text. "What am I looking at" is
about meaning and still belongs to the model.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

_READ = re.compile(
    r"""^(?:please\s+)?(?:hey\s+jarvis[,\s]*)?(?:
        (?:what\s+does|what'?s)\s+(?:it|this|my\s+screen|the\s+screen)\s+say|
        (?:read|read\s+out)\s+(?:me\s+)?(?:my|the|this)\s+screen|
        (?:what(?:'?s|\s+is))\s+(?:on\s+)?(?:my|the)\s+screen|
        (?:what\s+text\s+is\s+(?:on|shown)\s+(?:on\s+)?(?:my|the)\s+screen)|
        screen\s+text
    )\s*\??$""",
    re.IGNORECASE | re.VERBOSE,
)

# The other question: about meaning, not wording. Left to the vision model.
_LOOKS = re.compile(
    r"""^(?:please\s+)?(?:hey\s+jarvis[,\s]*)?(?:
        what\s+am\s+i\s+looking\s+at|
        (?:describe|explain)\s+(?:my|the|this)\s+screen|
        look\s+at\s+(?:my|the)\s+screen
    )\s*\??$""",
    re.IGNORECASE | re.VERBOSE,
)


def wants_the_words(text: str) -> bool:
    return bool(_READ.match((text or "").strip().rstrip(".!?")))


def wants_a_description(text: str) -> bool:
    return bool(_LOOKS.match((text or "").strip().rstrip(".!?")))


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not mine'."""
    from .vision import ocr

    if wants_the_words(text):
        if not ocr.available():
            return None                    # no reader; let the model try
        words = await asyncio.to_thread(ocr.read)
        if not words:
            return "I can't read any text on the screen just now, sir."
        return ocr.describe(words)

    if wants_a_description(text):
        from .integrations import lens

        described = await asyncio.to_thread(lens.screen, "what am I looking at")
        return described or "I couldn't make sense of the screen, sir."

    return None
