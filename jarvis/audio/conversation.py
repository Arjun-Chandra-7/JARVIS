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
    r"i'?m done|done|bas|bas itna|bas ho gaya|ho gaya|theek hai bas|chalo bye|bye|bye bye|goodbye|"
    r"ok(?:ay)? bye|stop listening|you can stop listening|rehne do|rehne de|"
    r"thanks?(?: jarvis)?|thank you(?: jarvis)?|shukriya)[.!]?$")

# The same words at the END of a longer sentence: "message Papa that I'll be late, then bye".
# The request is the sentence; the goodbye only says what to do after it. Anchored to the end and
# separated from what precedes it, so "send 'bye' to Papa" and "say goodbye to her" keep their
# words. Deliberately narrower than _END: "done", "nothing" and "rehne do" at the end of a
# sentence are usually part of it.
_TRAILING_CLOSE = re.compile(
    r"(?i)(?:[.!?,;]\s*|\s+(?:and|then|and then)\s+|[.!?,;]\s*(?:and\s+|then\s+|and then\s+)?)"
    r"(?:ok(?:ay)?[,\s]+)?(?:thanks?[,\s]+|thank you[,\s]+)?"
    r"(?:bye(?:\s*bye)?|goodbye|that'?s all|that'?s it|stop listening|bas|chalo bye|"
    r"thanks?(?: jarvis)?|thank you(?: jarvis)?|shukriya)[.!\s]*$")

# When the last reply is waiting on a yes or no, these are answers to it — "never mind" cancels
# the held message — not a goodbye. The approval manager decides; the conversation only listens.
_ANSWER_NOT_GOODBYE = re.compile(
    r"(?i)^(?:no(?:,)? thanks?|no thank you|never ?mind|rehne d[oe]|nothing(?: else)?|nahi|no)[.!]?$")


def is_session_end(text: str) -> bool:
    """The whole utterance is a goodbye and nothing else."""
    return bool(_END.match((text or "").strip()))


def split_closing(text: str) -> tuple[str, bool]:
    """(the request, whether the person also said goodbye).

    "ok now message 98… with country code plus 91. Bye." → ("ok now message 98… with country
    code plus 91", True). A sentence that is only a goodbye comes back as ("", True); one
    without a goodbye comes back unchanged with False. Quoted words are never taken:
    "send 'bye' to Papa" is a message whose text is bye.
    """
    said = (text or "").strip()
    if not said:
        return "", False
    if _END.match(said):
        return "", True
    m = _TRAILING_CLOSE.search(said)
    if not m:
        return said, False
    head = said[:m.start()].rstrip(" ,;")
    if not head or head.count("'") % 2 or head.count('"') % 2 or head.count("‘") != head.count("’"):
        return said, False       # the goodbye sits inside a quotation
    return head, True

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
            if self.expecting_answer and _ANSWER_NOT_GOODBYE.match(said):
                return ACT
            return END
        if self.state is not State.FOLLOW_UP_WINDOW or self.expecting_answer:
            return ACT
        if all(w in _FILLER for w in words):
            return IGNORE
        # Addressed by name again: certainly for us.
        if words[0] in {"jarvis", "hey"}:
            return ACT
        return ACT if len(words) >= 2 or words[0] in _ONE_WORD_COMMANDS else IGNORE
