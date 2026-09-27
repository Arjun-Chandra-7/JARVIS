"""One owner for the microphone at a time.

The voice loop reads one stream. Before this, whichever loop happened to be calling ``mic.read``
owned it: a dictation typed while the wake listener was also reading would lose frames to it,
and Jarvis's own voice could land in the next capture.

    IDLE ── start ──▶ WAKE_LISTENING ◀──────────────────────────────┐
                          │ wake / push-to-talk                     │ release
                          ▼                                         │
                    ASSISTANT_CAPTURE ──▶ PROCESSING ──▶ PLAYBACK ──┤
                          │ dictation key (preempts)                │
                          ▼                                         │
                    DICTATION_CAPTURE ──▶ PROCESSING ───────────────┘

Rules the tests hold it to: only one owner; dictation preempts wake listening and an
assistant capture, and waits for playback to be cut; a session that is never released gives the
microphone back after ``lease_s``; and a transcript captured under dictation can never be
handed to the assistant (``owner_of`` says whose it was).
"""
from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Callable, Optional


class MicState(str, Enum):
    IDLE = "idle"
    WAKE_LISTENING = "wake_listening"
    ASSISTANT_CAPTURE = "assistant_capture"
    DICTATION_CAPTURE = "dictation_capture"
    PROCESSING = "processing"
    PLAYBACK = "playback"


class Preempted(Exception):
    """Raised inside an assistant capture when dictation takes the microphone."""


class MicCoordinator:
    def __init__(self, lease_s: float = 180.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.lease_s = lease_s
        self.clock = clock
        self.state = MicState.IDLE
        self.owner = ""                    # "wake", "assistant", "dictation"
        self._since = clock()
        self._lock = threading.RLock()
        self._dictation_wanted = threading.Event()
        self.history: list[str] = []

    # ------------------------------------------------------------------ transitions
    def _go(self, state: MicState, owner: str) -> MicState:
        self.state, self.owner, self._since = state, owner, self.clock()
        self.history.append(state.value)
        del self.history[:-50]
        return state

    def listen_for_wake(self) -> MicState:
        with self._lock:
            if self.owner == "dictation":
                return self.state              # the wake listener waits; it does not take over
            return self._go(MicState.WAKE_LISTENING, "wake")

    def assistant_capture(self) -> MicState:
        with self._lock:
            if self.owner == "dictation":
                raise Preempted("dictation owns the microphone")
            return self._go(MicState.ASSISTANT_CAPTURE, "assistant")

    def playback(self) -> MicState:
        with self._lock:
            return self._go(MicState.PLAYBACK, self.owner or "assistant")

    def processing(self) -> MicState:
        with self._lock:
            return self._go(MicState.PROCESSING, self.owner)

    # ------------------------------------------------------------------ dictation
    def request_dictation(self) -> None:
        """The key went down. Whoever is reading next hands over."""
        self._dictation_wanted.set()

    @property
    def dictation_requested(self) -> bool:
        return self._dictation_wanted.is_set()

    def cancel_request(self) -> None:
        self._dictation_wanted.clear()

    def begin_dictation(self) -> MicState:
        with self._lock:
            self._dictation_wanted.clear()
            return self._go(MicState.DICTATION_CAPTURE, "dictation")

    def end_dictation(self) -> MicState:
        """Finished, cancelled or crashed: the microphone goes back to the wake listener."""
        with self._lock:
            if self.owner != "dictation":
                return self.state
            return self._go(MicState.WAKE_LISTENING, "wake")

    def expire(self) -> bool:
        """Give the microphone back from a dictation nobody finished. True when it did."""
        with self._lock:
            if self.owner == "dictation" and self.clock() - self._since > self.lease_s:
                self._go(MicState.WAKE_LISTENING, "wake")
                return True
            return False

    def guard(self, read: Callable[[], list]) -> Callable[[], list]:
        """A ``mic.read`` for assistant captures that stops the moment dictation is wanted."""
        def guarded():
            if self._dictation_wanted.is_set():
                raise Preempted("dictation requested")
            return read()
        return guarded


COORDINATOR = MicCoordinator()
