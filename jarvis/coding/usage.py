"""How much of each coding agent is actually left.

This began as "you cannot know", which was wrong. Claude will say, from outside a session:

    $ claude --print "/usage"
    Current session: 44% used · resets Sep 18, 2am (Asia/Kolkata)
    Current week (all models): 46% used · resets Sep 22, 2:29pm (Asia/Kolkata)

Two windows, and the one that matters is whichever is fuller — being fine for the week is no
help when the five-hour session is spent. So "left" is 100 minus the larger of them.

Codex will not answer from outside. `codex exec "/status"` does not run the slash command; it
reads it as a prompt and cheerfully summarises the repository instead. Its status only exists
inside a live session, which is why it has to be typed into the terminal and read back off the
screen — the same route the answers take.

Antigravity offers nothing either way, and is assumed available rather than assumed empty, since
guessing it is empty would remove the last agent for no reason.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Optional

# Below this, an agent is not worth starting: it will stop partway through the job.
START_ABOVE = 15.0
# And below this, an agent already working should be wound up deliberately rather than cut off.
HAND_OVER_AT = 5.0

ASK = {"claude": "/usage", "codex": "/status", "agy": "/status"}


@dataclass(frozen=True)
class Left:
    percent: Optional[float]      # None when the agent will not say
    detail: str = ""

    @property
    def known(self) -> bool:
        return self.percent is not None

    def enough_to_start(self) -> bool:
        return self.percent is None or self.percent > START_ABOVE

    def nearly_out(self) -> bool:
        return self.percent is not None and self.percent <= HAND_OVER_AT


# "44% used", "46% used", "12% remaining", "3% left". Percentages of *usage* and of *remainder*
# both appear in the wild and mean opposite things, so which one was said is kept.
_USED = re.compile(r"(?i)(\d{1,3})\s*%\s*(?:of\s+\w+\s+)?(used|consumed|spent)")
_LEFT = re.compile(r"(?i)(\d{1,3})\s*%\s*(left|remaining|available)")


def read_percent(text: str) -> Optional[float]:
    """Percent remaining, from whatever an agent printed, or None.

    The fullest window wins. An agent with 5% of its five-hour session and 80% of its week is out
    of road right now, and the number that matters is the one about to stop the work.
    """
    if not text:
        return None
    used = [float(m.group(1)) for m in _USED.finditer(text)]
    left = [float(m.group(1)) for m in _LEFT.finditer(text)]
    candidates = [100.0 - u for u in used] + left
    sane = [c for c in candidates if 0.0 <= c <= 100.0]
    return min(sane) if sane else None


def claude_from_outside(timeout_s: int = 90) -> Left:
    """Claude answers /usage without a session, so it never needs the terminal."""
    try:
        done = subprocess.run(["claude", "--print", "/usage"],
                              capture_output=True, text=True, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        return Left(None, f"could not ask claude ({type(exc).__name__})")
    text = (done.stdout or "") + (done.returncode and (done.stderr or "") or "")
    percent = read_percent(text)
    if percent is None:
        return Left(None, "claude did not report a percentage")
    return Left(percent, _first_window(text))


_WINDOW = re.compile(r"(?im)^\s*(current (?:session|week)[^\n]*)$")


def _first_window(text: str) -> str:
    """The line worth repeating out loud, if there is one."""
    found = _WINDOW.findall(text or "")
    return found[0].strip() if found else ""
