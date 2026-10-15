"""«What's playing?» — answered from the media players themselves, without a model.

Asked "What's playing right now?", the Brain read "right now" as a request for the news and listed
cinema showtimes from Fandango. The answer is on this machine: every MPRIS player (Spotify, the
browser, VLC) reports its track to playerctl.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

_ASKS = re.compile(
    r"(?i)^\s*(?:(?:hey\s+)?jarvis[,\s]+)?(?:what(?:'s| is)\s+(?:this\s+song|playing|on)(?:\s+right\s+now|\s+now)?|"
    r"what\s+song\s+is\s+(?:this|playing|on)|which\s+song\s+is\s+(?:this|playing)|now\s+playing|"
    r"who\s+(?:sings|is\s+singing)\s+this|kaun\s+sa\s+gaana\s+(?:hai|chal\s+raha\s+hai)|ye\s+kaun\s+sa\s+gaana\s+hai)"
    r"[\s?.!]*$")

_FMT = "{{status}}\t{{playerName}}\t{{artist}}\t{{title}}"


def asks(text: str) -> bool:
    return bool(_ASKS.match(text or ""))


def players() -> list[dict]:
    try:
        out = subprocess.run(["playerctl", "-a", "metadata", "--format", _FMT],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = (line.split("\t") + ["", "", "", ""])[:4]
        status, player, artist, title = (p.strip() for p in parts)
        if title:
            rows.append({"status": status, "player": player, "artist": artist, "title": title})
    return rows


def describe(rows: list[dict]) -> str:
    playing = [r for r in rows if r["status"] == "Playing"]
    pick = (playing or [r for r in rows if r["status"] == "Paused"] or [None])[0]
    if pick is None:
        return "Nothing is playing right now, sir."
    who = f" by {pick['artist']}" if pick["artist"] else ""
    where = pick["player"].split(".")[0].capitalize() if pick["player"] else "a player"
    if pick["status"] == "Playing":
        return f"{pick['title']}{who}, on {where}."
    return f"Nothing's playing — {pick['title']}{who} is paused on {where}."


async def handle(text: str, config=None) -> Optional[str]:
    if not asks(text):
        return None
    import asyncio

    return describe(await asyncio.to_thread(players))
