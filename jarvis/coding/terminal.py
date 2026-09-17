"""Read back what the editor's terminal is showing.

Everything that makes this more than a typewriter needs it: asking an agent how much quota it has
left and reading the answer, noticing it printed a URL, hearing what it concluded so it can be
said out loud.

Done through the clipboard rather than by looking at the screen. OCR would work — there is a fast
reader in this project already — but a terminal is exactly the case where it is worst: proportional
guesses at monospaced text, `l` against `1`, `O` against `0`, and percentages that have to be read
as numbers. The editor will hand over the real characters if asked, and "Terminal: Select All"
followed by a copy asks it.

The clipboard is put back afterwards. Somebody's copied line vanishing because Jarvis wanted to
read a terminal is a small theft that is very annoying to debug.
"""

from __future__ import annotations

import time
from typing import Optional

from ..integrations import desktop_control as input_
from ..integrations import lens
from . import vscode

SETTLE_S = 0.5


def _clipboard() -> str:
    return (lens.clipboard() or "").strip()


def _set_clipboard(text: str) -> None:
    import subprocess

    for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        try:
            subprocess.run(argv, input=text.encode(), timeout=4, check=False)
            return
        except Exception:  # noqa: BLE001
            continue


def read(keep_clipboard: bool = True) -> Optional[str]:
    """Everything the focused terminal is showing, or None if it could not be read."""
    before = _clipboard() if keep_clipboard else ""

    if not vscode.palette("Terminal: Select All"):
        return None
    time.sleep(SETTLE_S)
    if not input_.press_keys("ctrl+shift+c"):
        return None
    time.sleep(SETTLE_S)
    text = _clipboard()

    # Leave the terminal as it was found: a selection left highlighted swallows the next thing
    # typed into it, which would be the prompt.
    input_.press_keys("escape")

    if keep_clipboard and before and before != text:
        _set_clipboard(before)
    return text or None


def tail(lines: int = 40) -> Optional[str]:
    """The last few lines, which is all anyone means by "what did it say"."""
    whole = read()
    if whole is None:
        return None
    kept = [line for line in whole.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])
