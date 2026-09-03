"""Notifications: console always, desktop (notify-send) when available, and optional voice announcer."""

from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Optional

_announce: Optional[Callable[[str], None]] = None


def set_announcer(fn: Optional[Callable[[str], None]]) -> None:
    """Set the spoken TTS announcer (e.g. VoiceSession._speak)."""
    global _announce
    _announce = fn


def notify(title: str, message: str = "", speak: bool = False) -> None:
    print(f"\n🔔 {title}\n   {message}\n")
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", title, message], check=False, timeout=5)
        except Exception:  # noqa: BLE001 - notifications are best-effort
            pass
    if speak and _announce:
        try:
            spoken_text = f"{title}. {message}" if message else title
            _announce(spoken_text)
        except Exception:  # noqa: BLE001
            pass
