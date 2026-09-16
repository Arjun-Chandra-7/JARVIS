"""Which specialist takes a request.

Scored, not classified by a model. Asking the brain which brain to use costs a round trip before
any work starts and gets it wrong in the way that matters most — sending a destructive request to
whichever specialist sounded closest. Words are cheap to count and the answer is inspectable,
which matters when the reply later says who handled it.
"""

from __future__ import annotations

import re
from typing import Optional

from .roster import BY_NAME, DEFAULT, ROSTER, Specialist

_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def score(text: str, specialist: Specialist) -> float:
    """How well this request fits this specialist."""
    said = " ".join(_words(text))
    if not said:
        return 0.0
    total = 0.0
    for cue in specialist.cues:
        if " " in cue:
            if cue in said:
                total += 2.0        # a phrase matching is worth more than a word
        elif re.search(rf"\b{re.escape(cue)}\b", said):
            total += 1.0
    # Longer requests naturally hit more cues; normalise so a short command is not outscored by a
    # rambling sentence that happens to contain one of the same words.
    return total / (1 + 0.04 * len(said.split()))


def pick(text: str) -> tuple[Specialist, float]:
    """The specialist for this request, and how confident that is."""
    said = text or ""

    # Anything destructive goes to the guardian regardless of what else it sounds like. This is
    # the one case where a near-miss is not merely unhelpful.
    guardian = BY_NAME["guardian"]
    if score(said, guardian) > 0:
        return guardian, 1.0

    ranked = sorted(((score(said, s), s) for s in ROSTER), key=lambda p: p[0], reverse=True)
    best, runner_up = ranked[0], ranked[1]
    if best[0] <= 0:
        return DEFAULT, 0.0
    # Confidence is the gap to the next one: two specialists scoring alike means the request
    # genuinely sits between them, and the caller may want to say so.
    gap = (best[0] - runner_up[0]) / best[0] if best[0] else 0.0
    return best[1], round(min(1.0, 0.4 + gap), 2)


def named(name: str) -> Optional[Specialist]:
    return BY_NAME.get((name or "").strip().lower())
