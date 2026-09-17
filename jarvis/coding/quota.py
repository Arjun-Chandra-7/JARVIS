"""How much of each coding agent is left — as far as any of them will actually say.

The intent was "only use an agent if it has more than 15% of its tokens left". That number is
not obtainable. None of the three CLIs reports a balance: `claude`, `codex` and `agy` each have
no usage or quota subcommand, and nothing on disk carries a percentage. What Claude does record,
in its session transcripts, is the moment it was refused:

    "quotaLimits":{"status":"rejected","resetsAt":1789464600,"rateLimitType":"five_hour", ...}

So what can be known is not "how much is left" but "is this one refusing right now, and until
when" — which answers the question the threshold was really asking: do not start a job on an
agent that is going to stop halfway through.

Reported as such rather than dressed up as a percentage. When a real balance becomes available
this is the one place that has to change.
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


_CODEX_REFUSAL = re.compile(r"(?i)(429|rate[ _-]?limit(?:ed)?|quota exceeded|usage limit)")


def _codex_standing() -> Standing:
    """Codex says nothing structured, so this looks for a refusal in the last hour of sessions."""
    cutoff = time.time() - 3600
    for path in _recent_files(CODEX_SESSIONS):
        if os.path.getmtime(path) < cutoff:
            continue
        try:
            with open(path, errors="ignore") as f:
                tail = f.read()[-20000:]
        except OSError:
            continue
        if _CODEX_REFUSAL.search(tail):
            return Standing(False, 0.0, "refused recently")
    return Standing(True)


def standing(provider: str) -> Standing:
    """Whether this agent is worth starting a job on."""
    import shutil

    if not shutil.which(provider):
        return Standing(False, 0.0, "not installed")
    try:
        if provider == "claude":
            return _claude_standing()
        if provider == "codex":
            return _codex_standing()
    except Exception:  # noqa: BLE001 — never let a quota guess stop the work
        return Standing(True)
    # Antigravity publishes nothing at all, and guessing it is empty would take away the last
    # agent for no reason.
    return Standing(True)


def usable() -> dict[str, bool]:
    """Which agents to consider, in the shape the roster wants."""
    return {name: standing(name).ready for name in ("claude", "codex", "agy")}


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
