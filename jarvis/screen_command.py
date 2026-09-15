"""Direct, grounded handling of short 'click this on my screen' requests."""

from __future__ import annotations

import asyncio
import re


def target_from(text: str) -> str | None:
    text = (text or "").strip().rstrip(".!?")
    # Keep the user's visible target even when they wrap the request in extra words such as
    # "use your DOM and open the playlist on my screen".
    match = re.search(
        r"\b(?:click|select|choose|tap|open)\s+(?:on\s+)?(?:the\s+|my\s+)?"
        r"(?P<target>.+?)\s+(?:on|in)\s+(?:my|the)\s+screen\b",
        text, re.IGNORECASE,
    )
    if match:
        target = match.group("target").strip()
    else:
        match = re.fullmatch(
            r"(?:please\s+)?(?:click|select|choose|tap)\s+(?:on\s+)?"
            r"(?:the\s+|my\s+)?(?P<target>.+?)(?:\s+please)?",
            text, re.IGNORECASE,
        )
        target = match.group("target").strip() if match else ""
    target = re.sub(r"\s+(?:right now|now|please)$", "", target, flags=re.IGNORECASE)
    if not target or target.casefold() in {"it", "this", "that", "here", "there"}:
        return None
    return target


async def handle(text: str, config) -> str | None:
    target = target_from(text)
    if target is None:
        return None
    from .integrations import desktop_control
    result = await asyncio.to_thread(desktop_control.click_target, target, "left", False, config)
    return result
