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


def _may_speak(category: str) -> bool:
    """Which preference gate applies to a spoken alert of this category."""
    from ..preferences import job_alerts_enabled, notifications_enabled
    if category == "critical":
        return True                      # emergencies bypass every mute
    if category == "job":
        return job_alerts_enabled()      # background-task completion has its own toggle
    return notifications_enabled()       # passive readouts (default)


def notify(title: str, message: str = "", speak: bool = False, category: str = "notification") -> None:
    print(f"\n🔔 {title}\n   {message}\n")
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", title, message], check=False, timeout=5)
        except Exception:  # noqa: BLE001 - notifications are best-effort
            pass
    if speak and _announce and _may_speak(category):
        try:
            spoken_text = f"{title}. {message}" if message else title
            _announce(spoken_text)
        except Exception:  # noqa: BLE001
            pass
