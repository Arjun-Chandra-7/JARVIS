"""One conversation: wake once, then keep talking until it is over.

    IDLE ──wake──▶ WAKE_DETECTED ──▶ ACTIVE_LISTENING ──heard──▶ THINKING ──▶ SPEAKING
                                            ▲                                   │  ╲ barge-in
                                            │                                   ▼   ▶ INTERRUPTED
                                            └────── FOLLOW_UP_WINDOW ◀──────────┘         │
                                                    │  silence / "that's all"             │
                                                    ▼                                     │
                                                  IDLE        ACTIVE_LISTENING ◀─────────┘

    SLEEPING: "go to sleep" — nothing but the wake phrase gets through.

The voice loop used to ask for the wake word before every turn unless ``JARVIS_FOLLOWUP`` was
set, and with it set it listened for six seconds and treated whatever it heard as a command.
"Jarvis, open YouTube" → "search Pythagoras" → "play the first one" needs the second and third to
work without the wake word, and it needs a cough, a "hmm" or the TV not to become a fourth.

This module decides; the loop does the listening and speaking. It keeps no audio and does no I/O,
so every rule here is tested directly.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    WAKE_DETECTED = "wake_detected"
    ACTIVE_LISTENING = "active_listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    FOLLOW_UP_WINDOW = "follow_up_window"
    SLEEPING = "sleeping"


ACT, IGNORE, END = "act", "ignore", "end"

# Said to close the conversation. Whole sentence only: "that's all I needed for the essay, now
# open Docs" is a request, not a goodbye.
_END = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|thanks?|thank you)[,\s]*)*(?:that'?s all|that'?s it|that will be all|"
    r"that'?ll be all|nothing(?: else)?|no(?:,)? thanks?|no thank you|never ?mind|we'?re done|"
    r"i'?m done|done|bas|bas itna|bas ho gaya|ho gaya|theek hai bas|chalo bye|bye|goodbye|"
    r"thanks?(?: jarvis)?|thank you(?: jarvis)?|shukriya)[.!]?$")

# Sounds and fillers that are not requests. In the follow-up window nobody has addressed Jarvis
# by name, so a transcript made only of these is the room, not the person.
_FILLER = {"hmm", "hm", "mm", "mhm", "uh", "um", "uhh", "umm", "ah", "oh", "huh", "er", "erm",
           "ok", "okay", "yeah", "yes", "no", "haan", "han", "ha", "achha", "acha", "accha", "hmm.",
           "so", "and", "the", "a", "right", "well", "like", "wow", "lol", "haha"}

# One word that is a whole request on its own, even without the name.
_ONE_WORD_COMMANDS = {"pause", "play", "stop", "next", "previous", "skip", "louder", "quieter",
                      "mute", "unmute", "resume", "back", "cancel", "again", "repeat", "search"}

# Whisper's well-known hallucinations on silence and background audio.
_PHANTOM = re.compile(
    r"(?i)^(?:thanks? for watching|thank you for watching|please subscribe|subscribe to (?:my|the) channel|"
    r"you|\[.*\]|\(.*\)|♪.*|\.+)[.!]*$")


@dataclass
class ConversationSession:
    """How long to keep listening after each reply, and whether what was heard is for us."""

    window_s: float = 8.0          # silence allowed after a reply before the conversation ends
    max_turns: int = 20            # a runaway loop (the TV) ends eventually even if each turn passes
    clock: callable = time.monotonic
    state: State = State.IDLE
    turns: int = 0
    started_at: float = 0.0
    window_opened_at: float = 0.0
    expecting_answer: bool = False   # the last reply asked something: a one-word answer counts
    history: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ transitions
    def _go(self, state: State) -> State:
        self.state = state
        self.history.append(state.value)
        del self.history[:-50]
        return state

    def wake(self) -> State:
        if self.state is State.SLEEPING:
            return self.state
        self.turns = 0
        self.started_at = self.clock()
        self._go(State.WAKE_DETECTED)
        return self._go(State.ACTIVE_LISTENING)

    def thinking(self) -> State:
        return self._go(State.THINKING)

    def speaking(self) -> State:
        return self._go(State.SPEAKING)

    def interrupted(self) -> State:
        """The person talked over the reply: stop speaking and listen to them."""
        self._go(State.INTERRUPTED)
        return self._go(State.ACTIVE_LISTENING)

    def replied(self, reply: str = "") -> State:
        """A reply finished. Keep listening, without the wake word, for ``window_s``.

        A reply that asked something — "Which Nikhil?", "Say send it to confirm" — makes the next
        short answer count, where it would otherwise be taken for the room.
        """
        text = (reply or "").strip().lower()
        self.expecting_answer = text.endswith("?") or "to confirm" in text or "which one" in text
        self.turns += 1
        if self.turns >= self.max_turns:
            return self.end()
        self.window_opened_at = self.clock()
        return self._go(State.FOLLOW_UP_WINDOW)

    def silence(self) -> State:
        """Nothing was said in the window."""
        return self.end()

    def end(self) -> State:
        return self._go(State.IDLE)

    def sleep(self) -> State:
        return self._go(State.SLEEPING)

    def wake_from_sleep(self) -> State:
        return self._go(State.IDLE)

    # ------------------------------------------------------------------ decisions
    @property
    def active(self) -> bool:
        return self.state in {State.ACTIVE_LISTENING, State.FOLLOW_UP_WINDOW, State.THINKING,
                              State.SPEAKING, State.INTERRUPTED, State.WAKE_DETECTED}

    def listen_for_s(self, default_after_wake: float) -> float:
        """How long the next capture should wait for speech to start."""
        return self.window_s if self.state is State.FOLLOW_UP_WINDOW else default_after_wake

    def judge(self, transcript: str) -> str:
        """ACT on it, IGNORE it and keep listening, or END the conversation.

        Right after the wake word everything is meant for Jarvis. In the follow-up window the
        person has not said the name, so only something that reads as a request counts.
        """
        said = (transcript or "").strip()
        words = re.findall(r"[\w']+", said.lower())
        if not said or not words or _PHANTOM.match(said):
            return IGNORE if self.state is State.FOLLOW_UP_WINDOW else ACT
        if _END.match(said):
            return END
        if self.state is not State.FOLLOW_UP_WINDOW or self.expecting_answer:
            return ACT
        if all(w in _FILLER for w in words):
            return IGNORE
        # Addressed by name again: certainly for us.
        if words[0] in {"jarvis", "hey"}:
            return ACT
        return ACT if len(words) >= 2 or words[0] in _ONE_WORD_COMMANDS else IGNORE
