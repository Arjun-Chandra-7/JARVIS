"""«open my LinkedIn» — the copilot, not the website.

LinkedIn, for this user, means the copilot that manages it: drafts, approvals, the posting
calendar, the network queue, the numbers. Opening linkedin.com instead is technically what was
asked for and never what was wanted — and that is what used to happen, because "open X" is
handled by the generic opener and LinkedIn is a website like any other.

So any request to open, see or go to LinkedIn — or to the professional dashboard, which is what
this person actually calls it — comes here first, and the words pick the screen:
ask for the profile and you get the profile, ask for drafts and you get approvals, ask for
nothing in particular and you get the dashboard.

Requests that are plainly about doing something rather than looking at something — posting,
drafting, scheduling, messaging — are left alone. Those already have tools of their own, and
turning "post this on LinkedIn" into "here is a dashboard" would be a step backwards.
"""

from __future__ import annotations

import re
from typing import Optional

# The screens the copilot offers, and the words that mean each one. Checked in order, so the
# more specific readings win: "my network analytics" is analytics, not network.
SCREENS: tuple[tuple[str, str], ...] = (
    ("analytics", r"analytic|stat|impression|follower|reach|engagement|performance|numbers"),
    ("approvals", r"approval|draft|pending|review|awaiting|queue"),
    ("calendar",  r"calendar|schedule|scheduled|upcoming|planned"),
    ("network",   r"network|connection|invit|request|people"),
    ("settings",  r"setting|config|preference|account"),
    # "My LinkedIn page" is the profile, and the words between "my" and "page" vary — "my linked
    # in page", "my LinkedIn public page" — so the word alone is enough here.
    ("profile",   r"profile|portfolio|\bpage\b|\bbio\b"),
)

DEFAULT_SCREEN = "dashboard"

# The mishearings matter more than the spelling. Taken from the real transcript: "open my
# lindin" was heard, missed by a pattern that only knew "linkedin", handed to the brain, and
# answered with "Opened Feed | LinkedIn" — the site, not the dashboard. The user's own checkout
# of the copilot is called "Linkdin", which is a fair indication of how the word arrives.
_LINKEDIN = re.compile(
    r"""(?ix)\b(?:
        linked\s*[-]?\s*in | linkedin | lin[kg]?d[ie]n | lind[ie]n | linkd?in |
        link\s*din | linked\s*din
    )\b""")

# The other name for the same thing. The person who uses this calls it "my professional
# dashboard", and that phrase contains no form of the word LinkedIn — so it missed the pattern
# below entirely, went to the generic opener, and opened a website. Their name for it is the one
# that has to work.
_PROFESSIONAL = re.compile(
    r"(?ix)\b(?:professional|work|career)\s+(?:dash(?:board)?|console|copilot|assistant|hub)\b")

# Asking to look at it.
_WANTS_TO_SEE = re.compile(
    r"""(?ix)\b(?:
        open | show | see | view | go\s+to | take\s+me\s+to | bring\s+up | pull\s+up |
        launch | start | check | look\s+at | my
    )\b""")

# Asking to do something with it. These keep their own handling.
_WANTS_TO_ACT = re.compile(
    r"""(?ix)\b(?:
        post | publish | write | draft(?:\s+a)? | compose | schedule | send | message | dm |
        reply | comment | connect\s+with | invite | approve | delete | capture | note\s+down
    )\b""")


def parse(text: str) -> Optional[str]:
    """The copilot screen to open, or None when this is not that request."""
    said = (text or "").strip()
    if not said or not (_LINKEDIN.search(said) or _PROFESSIONAL.search(said)):
        return None
    if _WANTS_TO_ACT.search(said):
        return None
    # A bare "LinkedIn" counts: said on its own it is not a remark about the company.
    if not _WANTS_TO_SEE.search(said) and len(said.split()) > 3:
        return None
    for screen, words in SCREENS:
        if re.search(words, said, re.IGNORECASE):
            return screen
    return DEFAULT_SCREEN


async def run(screen: str) -> str:
    import asyncio

    from .integrations import linkedin

    problem = await asyncio.to_thread(linkedin._ensure)
    if problem:
        return problem
    opened = await asyncio.to_thread(linkedin.open_console, screen)
    if not opened:
        return "I couldn't open a browser window for the LinkedIn copilot, sir."
    where = "copilot" if screen == DEFAULT_SCREEN else f"copilot's {screen} screen"
    return f"Opened your LinkedIn {where}, sir."


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not a LinkedIn request'."""
    screen = parse(text)
    if screen is None:
        return None
    try:
        return await run(screen)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't open the LinkedIn copilot — {type(exc).__name__}: {exc}"
