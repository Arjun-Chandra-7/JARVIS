"""«Hey Jarvis, study mode» — the distractions go, and they stay gone.

Closing Discord once achieves nothing; it is open again inside a minute, because the hand that
opens it is not really asking a question. So this is not a single sweep. It keeps watching, and
anything on the list that comes back is closed again, until you say the words that end it.

Browser tabs count. Netflix in a tab is Netflix, and a mode that closes the application while
leaving the tab open is theatre.

Deliberately not a blocker at the network or hosts-file level: nothing here needs a password,
nothing is left behind if Jarvis dies, and undoing it is one sentence rather than an edit to a
system file you will find confusing in a fortnight.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

# Matched against process names and window titles, case-insensitively.
DISTRACTING_APPS = (
    "discord", "whatsapp", "wasistlos", "whatsdesk",
    "steam", "lutris", "heroic", "minecraft", "battle.net", "epicgames", "gamescope",
    "netflix", "primevideo", "hotstar", "telegram-desktop",
)

# Matched against tab URLs and titles.
DISTRACTING_SITES = (
    "netflix.com", "discord.com", "web.whatsapp.com", "primevideo.com",
    "hotstar.com", "twitch.tv", "instagram.com", "reddit.com/r/",
    "store.steampowered.com", "epicgames.com", "crazygames", "poki.com",
)

# Never touched, whatever the list says: closing the editor or the terminal during study time
# would be the single most annoying thing this could possibly do.
PROTECTED = ("code", "vscode", "terminal", "gnome-terminal", "konsole", "alacritty", "kitty",
             "jarvis", "electron", "chrome", "opera", "firefox")


@dataclass
class Session:
    started: float = field(default_factory=time.time)
    closed_apps: list = field(default_factory=list)
    closed_tabs: list = field(default_factory=list)

    def minutes(self) -> int:
        return max(0, int((time.time() - self.started) / 60))


_on: Optional[Session] = None


def on() -> bool:
    return _on is not None


def session() -> Optional[Session]:
    return _on


def start() -> Session:
    global _on
    _on = Session()
    return _on


def stop() -> Optional[Session]:
    global _on
    was, _on = _on, None
    return was


# --------------------------------------------------------------------------- applications
def _running() -> list[tuple[int, str]]:
    try:
        out = subprocess.run(["ps", "-eo", "pid,comm"], capture_output=True, text=True,
                             timeout=6).stdout
    except Exception:  # noqa: BLE001
        return []
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            found.append((int(parts[0]), parts[1].strip()))
    return found


def _is_distracting(name: str) -> bool:
    low = name.lower()
    if any(keep in low for keep in PROTECTED):
        return False
    return any(bad in low for bad in DISTRACTING_APPS)


def close_apps() -> list[str]:
    """Close what is running that should not be. Returns what was closed."""
    closed = []
    for pid, name in _running():
        if not _is_distracting(name):
            continue
        try:
            # Asked to quit rather than killed, so it saves what it was doing. A game that
            # ignores this is closed the hard way on the next pass.
            subprocess.run(["kill", "-TERM", str(pid)], timeout=4, check=False)
            closed.append(name)
        except Exception:  # noqa: BLE001
            continue
    return closed


# --------------------------------------------------------------------------- browser tabs
def _looks_distracting(url: str, title: str) -> bool:
    haystack = f"{url} {title}".lower()
    return any(bad in haystack for bad in DISTRACTING_SITES)


# One judgement per video, remembered for as long as the session lasts. Without this the sweep
# asks the brain about the same lecture every few seconds for an hour.
_judged: dict[str, bool] = {}


def forget_judgements() -> None:
    _judged.clear()


async def _should_close(url: str, title: str) -> tuple[bool, str]:
    """Whether this tab goes, and the reason — asking the brain only when the words do not say."""
    from . import watching

    verdict = watching.verdict_for(url, title)
    if verdict.certain:
        return verdict.close, verdict.reason

    key = watching.video_id(url) or url
    if key in _judged:
        return _judged[key], "as judged earlier"

    educational = await _ask_the_brain(title)
    if educational is None:
        # No answer, so keep the bias the classifier already has: leave it open. Closing a
        # lecture somebody is midway through is the worse mistake.
        return False, "could not tell, so left alone"
    _judged[key] = not educational
    return (not educational), ("looks like a lecture" if educational else "not educational")


async def _ask_the_brain(title: str) -> Optional[bool]:
    """One yes-or-no about a video title. None when there is no answer to be had.

    Deliberately not a conversation: a single constrained question, answered in one word, so a
    model cannot talk itself into an essay about whether learning is subjective.
    """
    from ..modes import watching

    clean = watching._clean_title(title)
    if not clean:
        return None
    question = (
        "A student is in study mode. Is this YouTube video educational — a lecture, tutorial, "
        "explanation, documentary or exam preparation — rather than entertainment?\n"
        f"Title: {clean}\n"
        "Answer with exactly one word: YES or NO."
    )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=12) as client:
            reply = await client.post("http://127.0.0.1:8770/chat",
                                      json={"message": question, "session_id": "study"})
        said = (reply.json() or {}).get("reply", "")
    except Exception:  # noqa: BLE001
        return None
    said = said.strip().upper()
    if said.startswith("YES"):
        return True
    if said.startswith("NO"):
        return False
    return None


async def close_tabs() -> list[str]:
    """Close distracting tabs in whichever browser Jarvis can talk to.

    YouTube is judged rather than blanket-closed — see `watching` — because study mode that
    closes the lecture is study mode nobody turns on.
    """
    from ..integrations import browser

    closed: list[str] = []
    try:
        targets = await browser._targets()
    except Exception:  # noqa: BLE001 — no browser control is not a failure of study mode
        return closed
    for target in targets:
        if target.get("type") != "page":
            continue
        url, title = target.get("url", ""), target.get("title", "")
        close, _why = await _should_close(url, title)
        if not close:
            continue
        if not await browser.close_tab(target.get("id", "")):
            continue
        closed.append(title[:50] or url[:50])
    return closed


async def enforce() -> tuple[list[str], list[str]]:
    """One sweep. Returns (apps closed, tabs closed)."""
    if not on():
        return [], []
    apps = close_apps()
    tabs = await close_tabs()
    here = _on
    if here is not None:
        here.closed_apps.extend(apps)
        here.closed_tabs.extend(tabs)
    return apps, tabs


# --------------------------------------------------------------------------- what was said
_START = re.compile(r"(?i)\b(?:study|focus)\s*mode\b(?!\s*(?:off|exit|end|stop))")
_STOP = re.compile(r"(?i)\b(?:exit|leave|end|stop|quit|turn\s+off|off)\s+(?:the\s+)?"
                   r"(?:study|focus)\s*mode\b|\b(?:study|focus)\s*mode\s+(?:off|exit|end|over)\b")


# "What is study mode?" and "is study mode on?" ask about it; they do not ask for it.
_ASKING_ABOUT_IT = re.compile(
    r"(?ix)^\s*(?:what|what'?s|how|why|when|is|are|does|can|could|should|tell\s+me)\b")


def asked_to_start(text: str) -> bool:
    said = (text or "").strip()
    if _ASKING_ABOUT_IT.match(said):
        return False
    return bool(_START.search(said)) and not asked_to_stop(said)


def asked_to_stop(text: str) -> bool:
    return bool(_STOP.search(text or ""))
