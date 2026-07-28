"""End-of-day rollup: distill the day's raw timestamped journal into a clean summary.

The journal auto-logs every request line; this routine reads that noise and writes a tidy
'## Day summary' (what {user} actually did, decisions, open threads) back into today's note, so
future briefings and 'what did I do' answers read from a clean digest rather than raw lines.
"""

from __future__ import annotations

from ..agent.autonomous import run_once
from ..config import Config

_SYSTEM = (
    "You are Jarvis writing a concise end-of-day summary for {user}. Be factual and brief."
)

_PROMPT = (
    "Read today's journal note in Jarvis/journal/ (it has raw timestamped activity lines). Distill it "
    "into a clean digest: 3-6 bullets of what I actually did and decided today, plus any open threads "
    "or follow-ups. Append it to the SAME note under a '## Day summary' heading (create the heading if "
    "absent; replace an existing Day summary). Then reply with just the bullets, nothing else."
)


async def daily_rollup(config: Config) -> str:
    return await run_once(
        config, _PROMPT, system=_SYSTEM.format(user=config.user_name), effort="low"
    )
