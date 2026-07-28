"""Runtime-adjustable agent settings the user can change by voice ("switch to opus", "use high
effort"). A tool updates the shared Config and marks the agent dirty; JarvisAgent rebuilds its
options and reconnects before the next turn, so the change takes effect immediately.
"""

from __future__ import annotations

# Spoken alias -> Claude Agent SDK model alias. The SDK resolves these to concrete model ids.
_MODELS = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku"}
_EFFORTS = {"low", "medium", "high"}

_dirty = {"on": False}


def normalize_model(name: str) -> str | None:
    n = (name or "").strip().lower()
    for key, alias in _MODELS.items():
        if key in n:
            return alias
    return None


def normalize_effort(name: str) -> str | None:
    n = (name or "").strip().lower()
    if any(w in n for w in ("high", "max", "deep", "hard")):
        return "high"
    if any(w in n for w in ("medium", "mid", "balanced", "normal")):
        return "medium"
    if any(w in n for w in ("low", "fast", "quick", "light")):
        return "low"
    return None


def mark_dirty() -> None:
    _dirty["on"] = True


def take_dirty() -> bool:
    was = _dirty["on"]
    _dirty["on"] = False
    return was
