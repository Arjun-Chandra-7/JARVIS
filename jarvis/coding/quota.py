"""Which coding agents are worth starting, and why not when they are not.

The threshold asked for was "only if it has more than 15% of its tokens left", and that number is
now real rather than approximated: `usage.py` gets it from the agents themselves. What remains
here is the cheaper, blunter signal — an agent that is installed, and not currently refusing.

Both are used, cheapest first. A refusal already recorded on disk costs nothing to read and is
conclusive; asking an agent for a percentage costs a subprocess and a few seconds, so it is only
done for the agents that are still candidates after the free check.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

CLAUDE_TRANSCRIPTS = "~/.claude/projects/*/*.jsonl"
CODEX_SESSIONS = "~/.codex/sessions/**/*.jsonl"

# Only look at recent files; a refusal from last week says nothing about now.
LOOK_BACK_S = 24 * 3600
# How many of the newest transcripts to read. A refusal is written to whichever conversation hit
# it, so the most recently touched few are where it will be.
NEWEST = 8


@dataclass(frozen=True)
class Standing:
    ready: bool
    resets_at: float = 0.0      # unix time the block lifts, when one is known
    reason: str = ""

    def waiting_minutes(self) -> int:
        return max(0, int((self.resets_at - time.time()) / 60)) if self.resets_at else 0


def _recent_files(pattern: str) -> list[str]:
    now = time.time()
    found = [p for p in glob.glob(os.path.expanduser(pattern), recursive=True)
             if now - os.path.getmtime(p) < LOOK_BACK_S]
    return sorted(found, key=os.path.getmtime, reverse=True)[:NEWEST]


_REFUSAL = re.compile(r'"quotaLimits"\s*:\s*\{(?P<body>[^}]*)\}')


def _claude_standing() -> Standing:
    """Refused, and until when, from the transcripts Claude writes as it goes."""
    latest = 0.0
    for path in _recent_files(CLAUDE_TRANSCRIPTS):
        try:
            with open(path, errors="ignore") as f:
                for line in f:
                    if "quotaLimits" not in line:
                        continue
                    found = _REFUSAL.search(line)
                    if not found:
                        continue
                    body = found.group("body")
                    if '"status":"rejected"' not in body.replace(" ", ""):
                        continue
                    when = re.search(r'"resetsAt"\s*:\s*(\d+)', body)
                    if when:
                        latest = max(latest, float(when.group(1)))
        except OSError:
            continue
    if latest > time.time():
        return Standing(False, latest, "rate limited")
    return Standing(True)


# Codex used to be guessed at by searching its recent sessions for the words "rate limit" or
# "429". That was wrong in the way heuristics usually are: it fired on a session that merely
# *discussed* rate limiting — including this project's own — and marked a perfectly healthy agent
# as refusing. Codex is asked directly now (see usage.ask_in_terminal), and when it cannot be
# asked it is treated as available rather than convicted on the presence of a phrase.


def standing(provider: str) -> Standing:
    """Whether this agent is worth starting a job on."""
    import shutil

    if not shutil.which(provider):
        return Standing(False, 0.0, "not installed")
    try:
        if provider == "claude":
            return _claude_standing()
    except Exception:  # noqa: BLE001 — never let a quota guess stop the work
        return Standing(True)
    # Antigravity publishes nothing at all, and guessing it is empty would take away the last
    # agent for no reason.
    return Standing(True)


def usable(check_balance: bool = True) -> dict[str, bool]:
    """Which agents to consider, in the shape the roster wants.

    An agent has to be installed, not currently refusing, and — when it will say — above the
    threshold worth starting on. An agent that will not say is kept: silence is not evidence of
    an empty account, and dropping it on a guess can leave nothing to do the work with.
    """
    from . import usage

    ready = {}
    for name in ("claude", "codex", "agy"):
        if not standing(name).ready:
            ready[name] = False
            continue
        if not check_balance:
            ready[name] = True
            continue
        ready[name] = usage.left_for(name).enough_to_start()
    return ready


def balances() -> dict[str, "object"]:
    """What each agent says it has left, for saying out loud."""
    from . import usage

    return {name: usage.left_for(name) for name in ("claude", "codex", "agy")}


def why_not(provider: str) -> Optional[str]:
    """A sentence about an agent that is being skipped, or None when it is fine."""
    state = standing(provider)
    if state.ready:
        return None
    if state.reason == "not installed":
        return f"{provider} isn't installed"
    minutes = state.waiting_minutes()
    if minutes:
        return f"{provider} is rate limited for another {minutes} minutes"
    return f"{provider} is {state.reason}"
