"""«close X» / «quit X» — the installed app, closed the way its own window would be.

Asked "Close the calculator.", the model proposed `pkill -f calculator` for approval. Approved, it
reported "(exit 0, no output)" and the calculator stayed open: `pkill -f` matched the very shell it
ran in — its command line contains "calculator" — killed that, and exited 0. "Close <app>" has one
meaning, so it is resolved against the same app index «open» uses and the app's own processes are
asked to quit (SIGTERM), matched by executable, never by a pattern over every command line.
"""

from __future__ import annotations

import os
import re
import signal
from pathlib import Path
from typing import Optional

_CLOSE_RE = re.compile(
    r"^(?:please\s+)?(?:close|quit|exit|shut\s+down|kill)\s+(?:the\s+|my\s+)?(?P<target>[\w .+-]{2,40}?)"
    r"(?:\s+(?:app|application|window|please|now))*[.!]*$", re.I)
# Not apps: tabs and windows are the browser's; "everything" is study mode's; the rest are Jarvis's
# own or the desktop's, and closing them would take the session down.
_NOT_AN_APP = re.compile(r"(?i)^(?:(?:this|that|the|current|all|other)\s+)?(?:tabs?|windows?|everything|it|that|this|all|"
                         r"door|eyes|deal|account|loop|gap|distance|conversation|session)$")
_NEVER = {"gnome-shell", "plasmashell", "kwin_wayland", "kwin_x11", "xwayland", "systemd", "bash", "zsh",
          "fish", "sh", "python", "python3", "node", "electron", "pipewire", "wireplumber", "dbus-daemon",
          "jarvis", "sudo", "login", "gdm", "sddm"}


def parse(text: str) -> Optional[str]:
    m = _CLOSE_RE.match((text or "").strip())
    if not m:
        return None
    target = m.group("target").strip()
    return None if _NOT_AN_APP.match(target) else target


def _pids_for(binary: str) -> list[int]:
    me = os.getpid()
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            continue
        if Path(exe).name == binary:
            found.append(int(entry.name))
    return found


def close(target: str) -> str:
    from .integrations import desktop_apps

    app = desktop_apps.resolve(target)
    if app is None or not app.binary:
        return f"I don't know an app called {target}, sir."
    binary = app.binary
    if binary.lower() in _NEVER:
        return f"I won't close {app.name} — it keeps the desktop running."
    pids = _pids_for(binary)
    if not pids:
        return f"{app.name} isn't open."
    closed = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            closed += 1
        except OSError:
            continue
    return f"Closed {app.name}." if closed else f"I couldn't close {app.name}."


async def handle(text: str, config=None) -> Optional[str]:
    target = parse(text)
    if target is None:
        return None
    from .integrations import desktop_apps

    if desktop_apps.resolve(target) is None:
        return None                         # not an app we know: leave it to the model
    import asyncio

    return await asyncio.to_thread(close, target)
