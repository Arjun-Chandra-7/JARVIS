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
    if not state["ok"]:
        return state["message"]

    # Say what actually came back rather than what was asked for: --restore-last-session is a
    # request to Chromium, not a guarantee, and claiming the tabs are back when they are not is
    # exactly the kind of thing this whole project has been correcting.
    await asyncio.sleep(3.0)
    try:
        pages = [t for t in await browser._targets()
                 if t.get("type") == "page" and t.get("url", "").startswith("http")]
    except Exception:  # noqa: BLE001
        pages = []
    if len(pages) > 1:
        return f"Opera GX is back under my control, with {len(pages)} of your tabs restored."
    if pages:
        return "Opera GX is back under my control. One tab came back."
    return ("Opera GX is back under my control, but your previous tabs did not come back — "
            "check its history if you need them.")
