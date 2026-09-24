"""Open a clean coding desktop on an explicit user command."""

from __future__ import annotations

import re
import shutil
import subprocess

from ..integrations import apps

_START = re.compile(r"(?i)\b(?:open|start|launch|set\s*up|switch\s*to)\s+(?:my\s+)?coding\s+(?:setup|workspace)\b")


def asked_to_start(text: str) -> bool:
    return bool(_START.search(text or ""))


def _windows() -> list[tuple[str, str]]:
    try:
        result = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, timeout=5)
    except OSError:
        return []
    windows = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 5:
            windows.append((parts[0], f"{parts[2]} {parts[4]}".lower()))
    return windows


def open_setup() -> dict:
    """Request normal window closes, then launch a fresh editor, music and ChatGPT."""
    closed = []
    for wid, identity in _windows():
        if "jarvis" in identity:
            continue
        try:
            result = subprocess.run(["wmctrl", "-i", "-c", wid], timeout=4, check=False)
            if result.returncode == 0:
                closed.append(wid)
        except OSError:
            pass

    opened = []
    editor = shutil.which("code") or shutil.which("code-insiders")
    if editor and apps._spawn([editor, "--new-window"]):
        opened.append("VS Code")
    if apps.launch_app("spotify"):
        opened.append("Spotify")
    from ..integrations import web_browser

    if apps.open_url("https://chatgpt.com/", browser=web_browser.preferred()):
        opened.append(f"ChatGPT in {web_browser._spoken(web_browser.preferred()) or 'the browser'}")
    return {"closed": len(closed), "opened": opened}
