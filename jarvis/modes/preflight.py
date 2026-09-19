"""What Iron Man mode needs, checked before it is needed.

The mode leans on six separate pieces of the desktop — a window lister, a window mover, a
terminal, a browser, the overlay and the backend — and any one of them missing produces the same
outcome: most of the sequence runs, one part quietly does not, and the reply still sounds like
success. That is the failure this whole project keeps re-learning, and the honest fix is to look
first and say what will not work while there is still time to do something about it.

Read-only. Nothing here starts, closes or moves anything; it answers "would this work" and leaves
the desktop exactly as it found it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from . import ironman


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    blocks: str = ""        # what stops working when this is missing; "" means nothing does


def _have(command: str) -> bool:
    return bool(shutil.which(command))


def _reaches(url: str, timeout: float = 2.5) -> bool:
    try:
        import httpx

        return httpx.get(url, timeout=timeout).status_code < 500
    except Exception:  # noqa: BLE001
        return False


def _an_editor_window() -> Optional[str]:
    for _wid, title in ironman._windows():
        if "visual studio code" in title.lower():
            return title
    return None


def check() -> list[Check]:
    """Every dependency, in the order the sequence uses them."""
    found = []

    found.append(Check(
        "window lister", _have("wmctrl"),
        "wmctrl lists and closes windows",
        blocks="nothing can be closed or found — the mode does essentially nothing"))

    found.append(Check(
        "window mover", _have("xdotool"),
        "xdotool moves windows the compositor will not",
        blocks="windows are found but some will not move into the frame"))

    terminal = ironman.TERMINAL_COMMAND[0]
    found.append(Check(
        "terminal", _have(terminal), f"{terminal} opens the bottom-right pane",
        blocks="the terminal pane stays empty"))

    browser = ironman.CHATGPT_COMMAND[0]
    found.append(Check(
        "browser", _have(browser), f"{browser} opens ChatGPT as its own window",
        blocks="the ChatGPT pane stays empty"))

    editor = _an_editor_window()
    found.append(Check(
        "editor", editor is not None,
        editor or "no VS Code window is open",
        blocks="there is nothing to put in the main pane"))

    found.append(Check(
        "backend", _reaches("http://127.0.0.1:8770/health"),
        "the backend serves the overlay its data",
        blocks="the frame appears with empty gauges and no projects"))

    found.append(Check(
        "audio", _have("aplay"), "aplay plays the power-up sting",
        blocks="the sequence runs silently"))

    return found


def blockers() -> list[Check]:
    """Only the checks that failed."""
    return [c for c in check() if not c.ok]


def spoken_warning() -> str:
    """One sentence about what will not work, or "" when everything is in place.

    Named for what it is for: this gets said out loud before the sequence starts, so the answer
    to "why is the terminal pane empty" arrives before the question.
    """
    missing = blockers()
    if not missing:
        return ""
    if len(missing) == 1:
        return f"Heads up, sir — {missing[0].blocks}."
    parts = "; ".join(c.blocks for c in missing[:3])
    return f"Heads up, sir — {parts}."


def report() -> str:
    """The whole picture, for reading rather than hearing."""
    lines = ["Iron Man mode preflight:", ""]
    for c in check():
        mark = "ok  " if c.ok else "MISSING"
        lines.append(f"  [{mark}] {c.name:<14} {c.detail}")
        if not c.ok and c.blocks:
            lines.append(f"            -> {c.blocks}")
    missing = blockers()
    lines.append("")
    lines.append("Everything Iron Man mode needs is present."
                 if not missing else
                 f"{len(missing)} of {len(check())} missing — the mode will run, partially.")
    return "\n".join(lines)
