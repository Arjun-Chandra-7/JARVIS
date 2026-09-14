"""Context lens: point JARVIS at what you are actually looking at.

Asking "what does this error mean?" only works if JARVIS can see the error. The lens captures one
scoped piece of context — the text you have selected, the clipboard, or the screen — hands it back
so the overlay can show you exactly what was captured, and attaches it to your next message.

Scope is the point. The capture happens when you ask for it, you can see what was taken before it
is sent, and it applies to one request. Nothing watches the screen in the background.

On this desktop (GNOME/Wayland) the primary selection is read with `wl-paste --primary`, which
needs no extra permission and no new dependency — selecting text in any application is enough.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional

MAX_CHARS = 6000


def _env() -> dict:
    import os

    env = dict(os.environ)
    # Snap/conda library paths break the Wayland clipboard tools.
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    return env


def _run(argv: list[str], timeout: float = 4.0) -> Optional[str]:
    if not shutil.which(argv[0]):
        return None
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, env=_env())
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    text = r.stdout.decode("utf-8", errors="replace").strip()
    return text or None


def selection() -> Optional[str]:
    """Text highlighted in any application right now (the X11/Wayland primary selection)."""
    for argv in (
        ["wl-paste", "--primary", "--no-newline"],
        ["xclip", "-selection", "primary", "-o"],
        ["xsel", "-p"],
    ):
        text = _run(argv)
        if text:
            return text
    return None


def clipboard() -> Optional[str]:
    for argv in (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "-b"],
    ):
        text = _run(argv)
        if text:
            return text
    return None


def active_window() -> Optional[str]:
    """Title of the focused window, for 'what should I do here?' style questions."""
    text = _run([
        "gdbus", "call", "--session", "--dest", "org.gnome.Shell",
        "--object-path", "/org/gnome/Shell", "--method", "org.gnome.Shell.Eval",
        "global.display.focus_window.get_title()",
    ])
    if text and "true" in text:
        # Reply looks like: (true, '"Title"')
        start, end = text.find('"'), text.rfind('"')
        if 0 <= start < end:
            return text[start + 1 : end].strip('\\"')
    for argv in (["xdotool", "getactivewindow", "getwindowname"], ["xprop", "-root", "_NET_ACTIVE_WINDOW"]):
        got = _run(argv)
        if got and argv[0] == "xdotool":
            return got
    return None


def screen(question: str = "", config=None) -> Optional[str]:
    """A textual reading of the screen, via the screenshot path plus a vision model."""
    from ..config import CONFIG
    from ..vision import analyze, screenshot

    path = screenshot.capture("/tmp")
    if not path:
        return None
    described = analyze.describe(
        path,
        question or "Describe what is on this screen, including any visible text, errors and "
                    "which application is in focus.",
        config or CONFIG,
    )
    return described or None


def capture(kind: str) -> dict:
    """Return {ok, kind, text} for one lens. Never raises — the overlay shows the error."""
    kind = (kind or "").lower()
    if kind == "selection":
        text = selection()
        if not text:
            return {"ok": False, "kind": "selection",
                    "error": "Nothing is selected. Highlight some text, then try again."}
        return {"ok": True, "kind": "selection", "text": text[:MAX_CHARS]}

    if kind == "clipboard":
        text = clipboard()
        if not text:
            return {"ok": False, "kind": "clipboard", "error": "The clipboard is empty."}
        return {"ok": True, "kind": "clipboard", "text": text[:MAX_CHARS]}

    if kind == "window":
        title = active_window()
        if not title:
            return {"ok": False, "kind": "window",
                    "error": "Could not read the focused window title on this desktop."}
        return {"ok": True, "kind": "window", "text": title[:MAX_CHARS]}

    if kind == "screen":
        text = screen()
        if not text:
            return {"ok": False, "kind": "screen",
                    "error": "Could not read the screen — screenshot or vision model unavailable."}
        return {"ok": True, "kind": "screen", "text": text[:MAX_CHARS]}

    return {"ok": False, "kind": kind, "error": f"Unknown lens '{kind}'."}
