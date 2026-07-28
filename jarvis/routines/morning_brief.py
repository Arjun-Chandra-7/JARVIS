"""The morning briefing routine: a short spoken summary of the day."""

from __future__ import annotations

from ..agent.autonomous import run_once
from ..config import Config

_SYSTEM = (
    "You are Jarvis giving {user} a concise spoken morning briefing. Warm but brief — 4 to 6 short "
    "sentences, no lists or markdown, suitable to be read aloud."
)

_PROMPT = (
    "Give me my morning briefing. Include: today's date and day of week; a one-line recap of what I "
    "did yesterday (read yesterday's note in Jarvis/journal/ — it has timestamped activity); what's "
    "on for today (my Google agenda if connected, plus any open tasks '- [ ]' under Projects/); and a "
    "one-line weather for my city plus one notable, relevant news item (use web search). Keep it to a "
    "few spoken sentences."
)


async def morning_brief(config: Config) -> str:
    return await run_once(
        config, _PROMPT, system=_SYSTEM.format(user=config.user_name), effort="medium"
    )
