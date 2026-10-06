"""Keeping an eye on the microphone while Jarvis waits for its name.

The turn path already mends a broken microphone (`inputs.recover`), but it only runs after a turn
has failed — and while Jarvis is waiting for the wake word, no turn ever starts. Two things went
wrong there, found in the log of 5–6 October:

* the capture stream raised PortAudioError on every read, the loop caught it and read again at
  once, 8,730 times in an hour, and the stream was never reopened;
* the microphone was muted at the system level, so the wake word listener heard digital silence
  for a day and a half. To that listener a muted microphone is just a quiet room.
"""
from __future__ import annotations

from typing import Callable, Optional

from . import inputs

# Doubling from a quarter second to a ceiling of ten: quick enough that a device coming back is
# heard within a few seconds, slow enough that a device that has gone for good costs nothing.
FIRST_WAIT_S = 0.25
LONGEST_WAIT_S = 10.0
# A single failed read is a hiccup. Several in a row mean the stream underneath is gone, and
# reading it again will not bring it back — reopening will.
REOPEN_AFTER = 3


class ReadBackoff:
    """How long to wait after a failed microphone read, and when to reopen the stream."""

    def __init__(self) -> None:
        self.failures = 0

    def failed(self) -> tuple[float, bool]:
        """Note one failed read. Returns (seconds to wait, whether to reopen the stream now)."""
        self.failures += 1
        wait = min(LONGEST_WAIT_S, FIRST_WAIT_S * 2 ** (self.failures - 1))
        return wait, self.failures % REOPEN_AFTER == 0

    def worked(self) -> Optional[int]:
        """Note a good read. Returns how many reads had failed before it, when any had."""
        failed, self.failures = self.failures, 0
        return failed or None


def open_at_start(is_muted: Callable[[], Optional[bool]] = inputs.is_muted,
                  unmute: Callable[[], bool] = inputs.unmute) -> Optional[str]:
    """Unmute the microphone when the voice assistant starts. Returns what to say, if anything.

    Starting the voice assistant is asking to be heard, so a mute left over from a call or a
    stray key press is lifted rather than obeyed — the same call `inputs.recover` already makes
    after a failed turn, made before the wait for the wake word instead of after a turn that
    can never begin.
    """
    if not is_muted():
        return None
    if unmute():
        return "Your microphone was muted, sir. I've unmuted it."
    return ("Your microphone is muted and I couldn't unmute it, sir. "
            "The mute key or Settings will do it.")
