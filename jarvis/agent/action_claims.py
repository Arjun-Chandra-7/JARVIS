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
        (?:i\s*(?:'ve|\s+have|\s+am|'m)?\s+)?  # "I", "I've", "I have", "I'm" — all optional
        (?:now\s+)?
        (?P<verb>
            open(?:ing|ed)? | launch(?:ing|ed)? | start(?:ing|ed)? |
            play(?:ing|ed)? | sent | sending | set(?:ting)? |
            click(?:ing|ed)? | search(?:ing|ed)? | turn(?:ing|ed)\s+(?:on|off) |
            mut(?:ing|ed) | paus(?:ing|ed) | creat(?:ing|ed) |
            chang(?:ing|ed) | adjust(?:ing|ed) | lower(?:ing|ed) | rais(?:ing|ed) |
            turn(?:ing|ed)(?:\s+\w+){0,3}\s+(?:up|down)
        )
        \b
    )
    """,
)

# The same assertion with the setting as the subject: "Brightness set to 30%", "Volume is now 40%".
# These carry no "I", so the clause-start pattern above never saw them, and a refused brightness
# change was delivered as "Brightness set to 30%." while the backlight stayed where it was.
_CLAIM_ABOUT_A_SETTING = re.compile(
    r"""(?ix)
    \b(?P<what> brightness | volume | sound | screen | display | backlight | keyboard )\b
    \s+
    (?: is | has\s+been | was | now )? \s*
    (?: set | turned | changed | adjusted | lowered | raised | at | now )
    \b
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
    return bool(_CLAIM.search(text) or _CLAIM_ABOUT_A_SETTING.search(text))


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


def honest_fallback(reason: str = "") -> str:
    """Used when the model still will not act: better to admit it than to claim success.

    When a tool said exactly why it could not work, that reason is worth far more than the
    admission — "nothing was carried out" leaves the user with nothing to do about it, while the
    brightness blocker names the group to join and the command that joins it.
    """
    admission = "I didn't actually manage to do that, sir"
    reason = re.sub(r"^\[FAILURE\]\s*", "", (reason or "").strip())
    if reason:
        # Left as written: lower-casing the first letter turned "I can read the brightness" into
        # "i can read the brightness".
        return f"{admission} — {reason}"
    return f"{admission} — nothing was carried out."


# --------------------------------------------------------------- replies about something else
# Garbled speech that parses as no command at all leaves the small model casting about, and it
# reaches for whatever tool happens to be at the top of its shortlist. Observed verbatim, three
# times in one session, for three unrelated utterances:
#
#     you (voice)> Jarvis opened the first result then.
#     you (voice)> Hey, John, this is Open Wikipedia.
#     you (voice)> He always set my volume to 70%.
#     jarvis>      I cannot set brightness directly. Please use `open_app` for web browsers.
#
# Nobody mentioned brightness. Answering a question the user did not ask — in the vocabulary of
# the tool registry — is worse than admitting the words did not come through.
_SUBJECTS = ("brightness", "volume", "backlight", "keyboard")

# `open_app`, `browser_type`: an internal tool name has no meaning to the person listening.
_LEAKS_A_TOOL_NAME = re.compile(r"`[a-z][a-z0-9]*_[a-z0-9_]+`|\b(?:open_app|browser_\w+|web_search)\b")


def is_a_non_sequitur(request: str, reply: str) -> bool:
    """True when the reply answers about something the request never raised."""
    said, asked = (reply or "").lower(), (request or "").lower()
    if not said.strip():
        return False
    if _LEAKS_A_TOOL_NAME.search(said):
        return True
    for subject in _SUBJECTS:
        if subject in said and subject not in asked:
            # Only when the reply is *about* that subject, not merely mentioning it in passing.
            if re.search(rf"(?:can(?:not|'t)|unable to|don'?t)\s+\w*\s*{subject}", said) \
                    or re.search(rf"{subject}\s+(?:directly|is not|cannot)", said):
                return True
    return False


def misheard_fallback() -> str:
    """What to say when the words plainly did not come through."""
    return "Sorry sir, I didn't catch that — say it again?"


# --------------------------------------------------------------- was an action even asked for?
# The claim guard exists for "open Netflix" answered with "Opening Netflix…" while nothing opened.
# Applied to a question it is nonsense: no tool runs when someone asks how you are, and that is
# the correct outcome, not a failure to act. Observed in the log, twice in a minute:
#
#     you (voice)> How are you doing?
#     jarvis>      I didn't actually manage to do that, sir — nothing was carried out.
_ASKS_FOR_ACTION = re.compile(
    r"""(?ix)
    (?:^|\b)
    (?: open | launch | start | run | play | watch | put \s+ on | close | quit | kill |
        click | tap | press | select | choose | type | write | send | reply | message |
        search | find | look \s+ up | google | download | install | delete | remove |
        set | change | turn | mute | unmute | increase | decrease | raise | lower |
        create | make | add | schedule | remind | call | join | share | screenshot |
        scroll | drag | draw | fix | improve | restart | stop )
    \b
    """,
)

# Questions, even ones containing an action word ("what can you open?").
_IS_A_QUESTION = re.compile(
    r"""(?ix)^\s*(?:
        who | what | when | where | why | how | which | whose |
        is | are | am | was | were | do | does | did | can | could | will | would |
        should | shall | may | might | have | has | had
    )\b""",
)


def asks_for_an_action(request: str) -> bool:
    """True when the user asked for something to be done, rather than asked a question."""
    text = (request or "").strip()
    if not text:
        return False
    if _IS_A_QUESTION.match(text) and not re.match(r"(?i)^(?:can|could|will|would)\s+you\b", text):
        return False
    return bool(_ASKS_FOR_ACTION.search(text))
