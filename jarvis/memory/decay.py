"""Old notes should be harder to find, not impossible.

The vault only grows. A throwaway preference from two years ago sits in the index at the same
embedding distance as the standing one from last week, and similarity has no opinion about which
you meant. Conversations already have a staleness rule — `HISTORY_STALE_AFTER_S` stops last
night's topic being resumed this morning — but facts and notes had none.

The fix is a recency prior on the *score*, not a garbage collector. This matters and is easy to
get wrong: deleting old notes answers "what is true now" and destroys "what did I think in
March", which is the same mistake closing a fact's window avoids. Nothing here removes anything.
A note from last year is still found when it is the only thing that matches; it just stops
outranking this week's note when both do.

The curve
---------
An exponential half-life, which is the shape forgetting actually has and the one every system in
this area converges on regardless of what it calls itself:

    weight = 0.5 ** (age_in_days / half_life_days)

Importance flattens it rather than exempting it, so a note marked important still fades — just
much more slowly. An exemption would recreate the original problem for exactly the notes most
likely to be re-read.

`HALF_LIFE_DAYS` of 180 is chosen so a six-month-old note is worth half a fresh one and a
two-year-old note about a fifth. That is enough to break ties and nowhere near enough to hide
something you actually asked for.
"""

from __future__ import annotations

import time
from typing import Optional

HALF_LIFE_DAYS = 180.0

# How far importance can stretch the half-life. At 1.0 an important note fades five times more
# slowly than an ordinary one — noticeable, and still finite.
IMPORTANCE_STRETCH = 4.0

DAY_SECONDS = 86400.0

# Below this, a result is effectively unreachable, and a floor is kinder than an asymptote: it
# means a genuinely old note can still win if nothing newer matches at all.
MIN_WEIGHT = 0.05


def weight(modified_at: Optional[float], now: Optional[float] = None,
           importance: float = 0.0, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """How much a note's score should count, given its age.

    `modified_at` is a unix timestamp. Unknown ages weigh 1.0 rather than 0: a note whose date we
    cannot read should not be buried for it.
    """
    if not modified_at:
        return 1.0
    moment = now if now is not None else time.time()
    age_days = max(0.0, (moment - modified_at) / DAY_SECONDS)
    stretched = half_life_days * (1.0 + max(0.0, min(1.0, importance)) * IMPORTANCE_STRETCH)
    if stretched <= 0:
        return 1.0
    decayed = 0.5 ** (age_days / stretched)
    return max(MIN_WEIGHT, min(1.0, decayed))


def apply(scored: list[tuple[str, float]], ages: dict[str, float],
          now: Optional[float] = None,
          importance: Optional[dict[str, float]] = None) -> list[tuple[str, float]]:
    """Re-rank (key, score) pairs by score times recency, best first.

    Returns pairs rather than keys so a caller can still see how much the decay moved something
    — a ranking you cannot explain is one nobody will trust when it surprises them.
    """
    importance = importance or {}
    out = [
        (key, score * weight(ages.get(key), now=now, importance=importance.get(key, 0.0)))
        for key, score in scored
    ]
    return sorted(out, key=lambda pair: -pair[1])
