"""Take the customer-service trailer off a spoken reply.

Every reply in the saved history ends the same way:

    jarvis> The color of the sky varies by time and weather conditions. How can I assist you
            further?
    jarvis> IlluminaTech, NeoSphere, QuantumCore, CodeBreaker, SynapseNet. How can I assist you
            further?

Typed, it is merely padding. Spoken, it is a second sentence read aloud after every single
answer, and the voice loop cannot start listening again until it has finished saying it.

Narrow on purpose. It removes an offer of further assistance and nothing else: Jarvis asking
"which Mum did you mean?" is a real question that must survive, and so must any reply that is
*only* the trailer — trimming that to nothing would turn a poor answer into no answer.
"""

from __future__ import annotations

import re

# The offer, in the shapes a model reaches for. Anchored to the end, because the same words in
# the middle of a reply are doing something else.
_TRAILER = re.compile(
    r"""(?ix)
    (?:^|(?<=[.!?]))\s*
    (?:
        (?:so\s+)?(?:how|what)\s+(?:else\s+)?(?:can|may|might|would)\s+i\s+
            (?:help|assist|be\s+of\s+(?:help|assistance|service))[^.?!]*[.?!]? |
        (?:is\s+there\s+)?anything\s+else[^.?!]*[.?!]? |
        (?:please\s+)?let\s+me\s+know\s+if\s+(?:you|there)[^.?!]*[.?!]? |
        (?:i'?m\s+)?(?:here\s+)?(?:to\s+help|if\s+you\s+need\s+anything)[^.?!]*[.?!]? |
        feel\s+free\s+to\s+ask[^.?!]*[.?!]?
    )
    \s*$
    """,
)


def trim_trailer(reply: str) -> str:
    """The reply without its offer of further assistance."""
    text = (reply or "").strip()
    if not text:
        return text
    # Twice: "Anything else? Let me know if you need anything." is two of them in a row.
    for _ in range(2):
        shorter = _TRAILER.sub("", text).strip()
        if not shorter or shorter == text:
            break
        text = shorter
    return text
