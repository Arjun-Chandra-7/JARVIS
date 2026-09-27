"""The Wayland clipboard, borrowed for one paste and put back exactly as it was.

wl-copy/wl-paste talk to GNOME's clipboard. What was there is saved in its own format (text,
an image, a file list — the first type offered) and restored afterwards, so pasting dictated text
does not cost the person whatever they had copied.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class Saved:
    mime: str = ""
    data: bytes = b""
    empty: bool = True


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def available() -> bool:
    return bool(shutil.which("wl-copy") and shutil.which("wl-paste"))


def save(timeout: float = 1.5) -> Saved:
    try:
        types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, timeout=timeout,
                               env=_env()).stdout.decode(errors="ignore").split()
    except (OSError, subprocess.SubprocessError):
        return Saved()
    if not types:
        return Saved()
    preferred = next((t for t in types if t.startswith("text/plain;charset=utf-8")), None) or \
        next((t for t in types if t in ("UTF8_STRING", "text/plain", "STRING")), None) or types[0]
    try:
        data = subprocess.run(["wl-paste", "--no-newline", "--type", preferred], capture_output=True,
                              timeout=timeout, env=_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return Saved()
    return Saved(mime=preferred, data=data, empty=False)


def put(text: str, mime: str = "text/plain;charset=utf-8", data: Optional[bytes] = None) -> bool:
    try:
        # wl-copy forks to serve the selection; it returns once the clipboard is owned.
        subprocess.run(["wl-copy", "--type", mime], input=data if data is not None else text.encode("utf-8"),
                       timeout=2, env=_env(), check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def read(timeout: float = 1.0) -> Optional[str]:
    try:
        return subprocess.run(["wl-paste", "--no-newline"], capture_output=True, timeout=timeout,
                              env=_env()).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None


def restore(saved: Saved) -> bool:
    if saved.empty:
        try:
            subprocess.run(["wl-copy", "--clear"], timeout=2, env=_env())
            return True
        except (OSError, subprocess.SubprocessError):
            return False
    return put("", mime=saved.mime, data=saved.data)


def paste_keys(terminal: bool = False) -> bool:
    """Ctrl+V (Ctrl+Shift+V in a terminal) through ydotool."""
    from ..integrations import desktop_control
    # The wait before the clipboard is restored is the caller's (insert.py restores it later,
    # off the critical path), so this returns as soon as the keys are sent.
    return desktop_control.press_keys("ctrl+shift+v" if terminal else "ctrl+v")
