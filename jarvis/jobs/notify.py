"""Notifications: console always, desktop (notify-send) when available."""

from __future__ import annotations

import shutil
import subprocess


def notify(title: str, message: str = "") -> None:
    print(f"\n🔔 {title}\n   {message}\n")
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", title, message], check=False, timeout=5)
        except Exception:  # noqa: BLE001 - notifications are best-effort
            pass
