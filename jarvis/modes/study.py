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
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..preferences import state_dir

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


def _state_file() -> Path:
    return state_dir() / "study-mode.json"


def on() -> bool:
    return _state_file().exists()


def session() -> Optional[Session]:
    global _on
    if not on():
        _on = None
        return None
    try:
        started = float(json.loads(_state_file().read_text())["started"])
    except (OSError, ValueError, KeyError, TypeError):
        started = time.time()
    if _on is None or _on.started != started:
        _on = Session(started=started)
    return _on


def start() -> Session:
    global _on
    _on = Session()
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"started": _on.started}))
    os.chmod(temp, 0o600)
    temp.replace(path)
    return _on


def stop() -> Optional[Session]:
    global _on
    was = session()
    _on = None
    _state_file().unlink(missing_ok=True)
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
_EDUCATIONAL = re.compile(
    r"(?i)\b(ncert|cbse|class\s*(?:9|10|11|12)|chapter|lesson|lecture|tutorial|"
    r"explained|explanation|study|revision|exam|board|maths?|mathematics|science|"
    r"physics|chemistry|biology|history|geography|civics|economics|grammar|"
    r"solved|solution|derivation|learn|education|course|jee|neet)\b")
_pending_video_titles: dict[str, float] = {}


def _looks_distracting(url: str, title: str) -> bool:
    """Allow only recognisable educational YouTube videos; Shorts are always blocked."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.lower()
    if host in {"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}:
        if path.startswith("/shorts"):
            return True
        if host == "youtu.be" or path == "/watch":
            # Navigation publishes the URL before the title. Give the educational video a
            # moment to identify itself; otherwise every permitted video closes on load.
            if title.strip().lower() in {"", "youtube", "youtube - youtube"}:
                return time.monotonic() - _pending_video_titles.setdefault(
                    url, time.monotonic()) > 8
            _pending_video_titles.pop(url, None)
            return not bool(_EDUCATIONAL.search(title))
        return True
    return any(host == site or host.endswith("." + site)
               for site in ("netflix.com", "instagram.com")) or any(
                   bad in f"{url} {title}".lower() for bad in DISTRACTING_SITES)


async def close_tabs() -> list[str]:
    """Close distracting tabs in whichever browser Jarvis can talk to."""
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
        if not _looks_distracting(url, title):
            continue
        if not await _close_tab(target):
            continue
        closed.append(title[:50] or url[:50])
    return closed


async def _close_tab(target: dict) -> bool:
    """Close one tab through the debugging port, which is how Jarvis reaches the browser."""
    target_id = target.get("id", "")
    if not target_id:
        return False
    import httpx

    from ..integrations import browser

    debugger = target.get("webSocketDebuggerUrl", "")
    port = browser.relay().port if f":{browser.relay().port}/" in debugger else browser.DEBUG_PORT

    try:
        async with httpx.AsyncClient(timeout=4) as client:
            reply = await client.get(
                f"http://127.0.0.1:{port}/json/close/{target_id}",
                headers={"X-Jarvis-Study": "close"} if port == browser.relay().port else None)
        if reply.status_code != 200:
            return False
        return bool(reply.json().get("closed")) if port == browser.relay().port else True
    except Exception:  # noqa: BLE001
        return False


async def enforce() -> tuple[list[str], list[str]]:
    """One sweep. Returns (apps closed, tabs closed)."""
    if not on():
        return [], []
    apps = close_apps()
    tabs = await close_tabs()
    here = session()
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
