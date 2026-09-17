"""Drive the editor's integrated terminal the way a person would.

The agents can all be run headlessly — that is what `coding_jobs` does — and the point of doing
it this way instead is that you can see it happen: the terminal opens, the command appears, the
prompt is typed out, and the answer scrolls past in front of you. Nothing is hidden, and you can
take the keyboard back at any moment and carry on the conversation yourself.

The terminal is opened through the Command Palette rather than the keyboard shortcut. Ctrl+` is
one keystroke and would be the obvious choice, but the backtick has no entry in the key map the
input backend uses, so it cannot be pressed at all; "Terminal: Create New Terminal" reaches the
same place using keys that exist, and has the side benefit of being legible on screen.
"""

from __future__ import annotations

import subprocess
import time
from typing import Optional

from ..integrations import desktop_control as input_

# How long the editor needs to act on a keystroke before the next one is sent. Typing into a
# palette that has not opened yet puts the text in whatever had focus before — which, in an
# editor, means into your source file.
SETTLE_S = 0.6
PALETTE_S = 0.9
SHELL_S = 1.6


def window() -> Optional[str]:
    """The editor's main window id, or None when it is not running."""
    try:
        out = subprocess.run(["xdotool", "search", "--class", "code"],
                             capture_output=True, text=True, timeout=6).stdout
    except Exception:  # noqa: BLE001
        return None
    best = None
    for wid in out.split():
        try:
            name = subprocess.run(["xdotool", "getwindowname", wid],
                                  capture_output=True, text=True, timeout=4).stdout.strip()
        except Exception:  # noqa: BLE001
            continue
        # The real window is the one with a document title; the others are helpers called "code".
        if "visual studio code" in name.lower():
            best = wid
    return best


def active_window() -> Optional[str]:
    try:
        out = subprocess.run(["xdotool", "getactivewindow"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


def focus() -> bool:
    """Bring the editor to the front, and confirm it actually came.

    Asking is not the same as getting. With a full-screen video on top, `xdotool windowactivate`
    returns success, the video stays where it is, and everything typed afterwards goes to the
    video player — which is how a test of this function ended up sending a Command Palette
    shortcut and a shell command into an episode of Friends. The window that ends up with the
    keyboard is checked, not assumed.
    """
    wid = window()
    if not wid:
        return False
    try:
        subprocess.run(["xdotool", "windowactivate", "--sync", wid], timeout=8, check=False)
    except Exception:  # noqa: BLE001
        return False
    time.sleep(SETTLE_S)
    return active_window() == wid


def blocked_by() -> Optional[str]:
    """The window sitting in front of the editor, when one is, for saying so out loud."""
    wid, active = window(), active_window()
    if not wid or not active or active == wid:
        return None
    try:
        return subprocess.run(["xdotool", "getwindowname", active],
                              capture_output=True, text=True, timeout=4).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def palette(command: str) -> bool:
    """Run an editor command by name, as if typed into the Command Palette."""
    if not input_.press_keys("ctrl+shift+p"):
        return False
    time.sleep(PALETTE_S)
    if not input_.type_text(command):
        input_.press_keys("escape")
        return False
    time.sleep(SETTLE_S)
    ok = input_.press_keys("enter")
    time.sleep(SETTLE_S)
    return ok


def new_terminal() -> bool:
    """A fresh integrated terminal, focused and ready to type into."""
    if not palette("Terminal: Create New Terminal"):
        return False
    time.sleep(SHELL_S)     # the shell has to draw a prompt before it will accept a command
    return True


def type_line(text: str, enter: bool = True) -> bool:
    """Type one line into whatever has focus, slowly enough to be watched."""
    if not input_.type_text(text):
        return False
    if not enter:
        return True
    time.sleep(SETTLE_S)
    return input_.press_keys("enter")


def send_prompt(text: str) -> bool:
    """Type a prompt into an agent that is already running in the focused terminal.

    One line: a newline inside the text would submit half a prompt, so any the speech happened to
    contain are flattened. The agents all treat a single line as a complete turn.
    """
    one_line = " ".join((text or "").split())
    if not one_line:
        return False
    return type_line(one_line)
