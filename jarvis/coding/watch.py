"""Notice when the agent in the terminal has finished, and say what it concluded.

The prompt is typed and the turn ends; the agent then thinks for anywhere between ten seconds and
several minutes. Holding the conversation open for that would make Jarvis unusable for anything
else, and saying nothing at all means standing over the terminal to find out — which is the thing
this was supposed to remove.

So the answer is waited for in the background and announced when it arrives, the same way a
finished background job or an incoming message is.

"Finished" is judged by the output going quiet rather than by any marker. Each agent ends its
turn differently and all of them redraw their prompt while still working, so a settled screen —
unchanged across two looks, several seconds apart — is the signal that holds for all three.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

# Two looks this far apart with no change means it has stopped writing.
QUIET_FOR_S = 6.0
# Nothing is waited for longer than this; an agent still going after it is one to look at yourself.
GIVE_UP_AFTER_S = 15 * 60
# Nor is an answer announced before the agent has had a moment to start.
SETTLE_FIRST_S = 8.0


@dataclass
class Expected:
    agent: str
    work: str
    asked_at: float = field(default_factory=time.time)
    last_seen: str = ""
    last_change: float = field(default_factory=time.time)


@dataclass
class Finished:
    agent: str
    said: Optional[str]
    link: Optional[str]


_waiting: Optional[Expected] = None
_last_finished: Optional[tuple[str, float]] = None


def expect(agent: str, work: str) -> None:
    """Note that an answer is coming, so the watcher knows to look."""
    global _waiting, _last_finished
    _waiting = Expected(agent=agent, work=work)
    _last_finished = None


def waiting_for() -> Optional[str]:
    return _waiting.agent if _waiting else None


def forget() -> None:
    global _waiting
    _waiting = None


def recently_finished(agent: str, within_s: float = 30.0) -> bool:
    """Whether this agent's per-prompt result was just delivered.

    The process watcher and this watcher poll independently.  This small hand-off closes the
    timing window where both see the same completion and speak over each other.
    """
    if _last_finished is None:
        return False
    who, at = _last_finished
    from . import roster
    return roster.canonical_name(who) == roster.canonical_name(agent) and time.time() - at <= within_s


def check(read_terminal) -> Optional[Finished]:
    """One look. Returns what to announce, or None while the agent is still working.

    `read_terminal` is passed in rather than imported so this can be tested without an editor,
    and so a look never costs anything when nothing is expected.
    """
    global _waiting, _last_finished
    if _waiting is None:
        return None

    now = time.time()
    if now - _waiting.asked_at > GIVE_UP_AFTER_S:
        forget()
        return None
    if now - _waiting.asked_at < SETTLE_FIRST_S:
        return None

    screen = (read_terminal() or "").strip()
    if not screen:
        return None

    if screen != _waiting.last_seen:
        _waiting.last_seen = screen
        _waiting.last_change = now
        return None                      # still writing
    if now - _waiting.last_change < QUIET_FOR_S:
        return None                      # quiet, but not for long enough to be sure

    from . import presence
    if presence.classify_words(screen) == "asking":
        _waiting.last_change = now
        return None                      # paused to ask a question; not finished

    from . import answers

    done = Finished(agent=_waiting.agent,
                    said=answers.spoken_answer(screen),
                    link=answers.worth_opening(screen))
    _last_finished = (_waiting.agent, now)
    forget()
    return done
