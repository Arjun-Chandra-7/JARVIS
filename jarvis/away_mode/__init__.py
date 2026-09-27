"""Away mode: Jarvis answers messages as the owner's assistant — never as the owner — while they
are out, within a policy they approved, and briefs them when they are back.

    session.py     the AwaySession, thread state and the private state file
    intent.py      the owner's words → start / extend / stop / summary
    control.py     approval, lifecycle and the owner's live commands
    engine.py      the per-message pipeline (the only thing that replies)
    policy.py      topics, urgency, prompt-injection and reply checks
    language.py    English / Hindi / Hinglish detection and slang folding
    replies.py     deterministic replies with the JARVIS disclosure
    connectors.py  WhatsApp bridge, phone notifications, dry run
    escalation.py  desktop, phone and spoken alerts
    summary.py     the return briefing
    calls.py       call capabilities, the KDE Connect monitor and the call simulator
    daemon.py      the backend loop
"""
from __future__ import annotations

from typing import Any, Optional


def is_active(config) -> bool:
    from .session import is_active as _active
    return _active(config)


def status(config) -> dict[str, Any]:
    """For status displays in other modules: {away, until, reason}."""
    from .session import Store
    s = Store(config).active_session()
    if s is None:
        return {"away": False, "until": "", "reason": ""}
    return {"away": True, "until": s.return_clock(), "reason": "Away mode"}


def set_responder(responder) -> None:
    from .engine import set_responder as _set
    _set(responder)


def get_responder() -> Optional[Any]:
    from .engine import get_responder as _get
    return _get()
