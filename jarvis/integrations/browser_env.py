"""Run the automation browsers (ChatGPT, Perplexity) HIDDEN.

They can't be truly headless — Cloudflare blocks headless Chrome. So we keep the browser *headful*
(which passes the challenge) but make its window invisible:

1. If **Xvfb** is installed → render on a private virtual display (fully invisible, best option).
2. Otherwise → launch the real window positioned far off-screen + minimized (works with no install).

Controlled by JARVIS_BROWSER_HIDDEN (default "1" = hidden). Set it to "0" to show the windows.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time

_xvfb_proc: subprocess.Popen | None = None
_xvfb_display: str | None = None
_OFFSCREEN_ARGS = ["--window-position=-32000,-32000", "--window-size=1280,900", "--start-minimized"]


def hidden_enabled() -> bool:
    return os.environ.get("JARVIS_BROWSER_HIDDEN", "1").strip().lower() not in ("0", "false", "no")


def _ensure_xvfb() -> str | None:
    """Start a shared Xvfb once and return its display (":99"), or None if unavailable."""
    global _xvfb_proc, _xvfb_display
    if _xvfb_display:
        return _xvfb_display
    if not shutil.which("Xvfb"):
        return None
    display = os.environ.get("JARVIS_XVFB_DISPLAY", ":99")
    try:
        _xvfb_proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1280x900x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)  # let it come up
        if _xvfb_proc.poll() is not None:  # died immediately
            return None
        _xvfb_display = display
        atexit.register(_stop_xvfb)
        return display
    except Exception:  # noqa: BLE001
        return None


def _stop_xvfb() -> None:
    global _xvfb_proc
    if _xvfb_proc and _xvfb_proc.poll() is None:
        try:
            _xvfb_proc.terminate()
        except Exception:  # noqa: BLE001
            pass


def launch_extras() -> tuple[dict, list[str]]:
    """(env overrides, extra chrome args) for launch_persistent_context to run hidden.

    Always run headful (headless=False) at the call site; this just hides the window.
    """
    if not hidden_enabled():
        return ({}, [])
    display = _ensure_xvfb()
    if display:
        return ({**os.environ, "DISPLAY": display}, [])
    # no Xvfb → best-effort off-screen window (still headful, still passes Cloudflare)
    return ({}, list(_OFFSCREEN_ARGS))
