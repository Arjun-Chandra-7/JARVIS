"""«restart Opera with control» — the one way back when the browser is running without it.

Jarvis suggests this sentence every time a browser action fails, and it was reachable only by the
local 3B model choosing the right tool out of its shortlist — the least reliable path in the
system, and the reason the suggestion appeared over and over in one evening's log without the
thing it suggests ever happening.

Saying it is the consent. Restarting closes whatever tabs are open, which is why Jarvis will not
do it on its own initiative, but a person who says this sentence has decided.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

_ASK = re.compile(
    r"""^(?:please\s+)?(?:
        (?:restart|relaunch|reopen)\s+(?:the\s+)?(?:opera(?:\s*gx)?|browser|chrome)
            (?:\s+(?:with|and\s+enable)\s+control)?|
        (?:enable|turn\s+on|give\s+yourself)\s+(?:browser\s+)?control
            (?:\s+of\s+(?:the\s+)?(?:browser|opera(?:\s*gx)?))?|
        take\s+control\s+of\s+(?:the\s+)?(?:browser|opera(?:\s*gx)?)
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def wants_control(text: str) -> bool:
    return bool(_ASK.match((text or "").strip().rstrip(".!?")))


async def handle(text: str, _config=None) -> Optional[str]:
    """None means 'not mine'."""
    if not wants_control(text):
        return None
    from .integrations import browser

    if browser.control_ready():
        return "Opera GX is already under my control, sir."

    state = await asyncio.to_thread(browser.ensure, "", True)
    if state["ok"]:
        return "Opera GX is back, under control. Your previous tabs are closed."
    return state["message"]
