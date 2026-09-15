"""Catch a reply that claims an action nothing actually performed.

Observed live, repeatedly: "open opera gx" → "Opening Opera GX...", "open friends" → "Opening
Friends on Netflix..." — no tool called, nothing launched, and the user is told it happened. Then
the claim lands in the conversation history and the model repeats the pattern for everything.

A small model narrating instead of acting is not a prompt problem that stays fixed; it needs a
check after the fact. If the reply asserts that something was opened, launched, played, sent or
set, and no tool ran this turn, the reply is not true and must not be delivered as-is.

Deliberately narrow. It matches only first-person assertions about *this* turn — "opening X",
"I've launched X" — and not questions, offers, refusals, or descriptions of what could be done,
so a model that honestly says "I can open that for you" is left alone.
"""

from __future__ import annotations

import re

# Present/perfect assertions that something is being or has been done.
_CLAIM = re.compile(
    r"""(?ix)
    (?:^|[.!?]\s+|\band\s+|,\s*)          # start of a clause
    (?:
        (?:i\s*(?:'ve|\s+have|\s+am|'m)\s+)?   # optional "I've" / "I am"
        (?:now\s+)?
        (?P<verb>
            open(?:ing|ed)? | launch(?:ing|ed)? | start(?:ing|ed)? |
            play(?:ing|ed)? | sent | sending | set(?:ting)? |
            click(?:ing|ed)? | search(?:ing|ed)? | turn(?:ing|ed)\s+(?:on|off) |
            mut(?:ing|ed) | paus(?:ing|ed) | creat(?:ing|ed)
        )
        \b
    )
    """,
)

# Phrasings that are explicitly NOT a claim about this turn.
_NOT_A_CLAIM = re.compile(
    r"""(?ix)
    \b(
        would\s+you | do\s+you\s+want | shall\s+i | should\s+i | can\s+i | may\s+i |
        i\s+can | i\s+could | i\s+will | i'?ll | let\s+me\s+know | if\s+you | to\s+open |
        cannot | can'?t | unable | could\s+not | couldn'?t | failed | not\s+able |
        which\s+one | did\s+you\s+mean | please\s+specify | i\s+don'?t
    )\b
    """,
)


def claims_an_action(reply: str) -> bool:
    """True when the text asserts that an action was carried out in this turn."""
    text = (reply or "").strip()
    if not text:
        return False
    if _NOT_A_CLAIM.search(text):
        return False
    return bool(_CLAIM.search(text))


# Vague technical excuses a model reaches for when a tool failed with a perfectly specific
# reason. Observed: "No installed app matches Networks" was reported to the user as "It seems
# there's a temporary glitch" — which is not true, hides the real cause, and invites them to just
# try again forever.
_VAGUE_EXCUSE = re.compile(
    r"""(?ix)\b(
        temporary\s+(?:glitch|issue|problem|error) | technical\s+(?:glitch|issue|difficult\w*) |
        something\s+went\s+wrong | glitch | hiccup | on\s+my\s+end |
        try\s+again\s+(?:later|in\s+a\s+moment)
    )\b""",
)


def invents_an_excuse(reply: str) -> bool:
    """True when the reply blames a vague fault instead of saying what actually happened."""
    return bool(_VAGUE_EXCUSE.search((reply or "").strip()))


def real_reason(reply: str, tool_message: str) -> str:
    """Replace a vague excuse with what the tool actually reported."""
    reason = (tool_message or "").strip()
    if not reason:
        return reply
    reason = reason.replace("WRONG TOOL.", "").strip()
    return reason[:400]


def correction_for(reply: str) -> str:
    """What to tell the model when it claimed something it never did."""
    return (
        "STOP. You replied with \"" + (reply or "").strip()[:120] + "\" but you did not call any "
        "tool, so nothing actually happened and that reply would be a lie. Saying you are opening "
        "something does not open it. Call the correct tool NOW to actually perform the action. If "
        "no tool can do it, say plainly that you cannot."
    )


def honest_fallback() -> str:
    """Used when the model still will not act: better to admit it than to claim success."""
    return "I didn't actually manage to do that, sir — nothing was carried out."
