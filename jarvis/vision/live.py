"""Shared flag for live screen-share mode.

The agent's tools flip it on/off; the voice loop reads it and, while on, attaches a fresh
screenshot to every spoken turn so Jarvis can see and help with whatever is on screen in real time.
"""

from __future__ import annotations

_state = {"on": False}


def is_active() -> bool:
    return _state["on"]


def set_active(on: bool) -> None:
    _state["on"] = bool(on)
