"""One conversation: wake once, then keep talking until it is over.

The one authoritative conversation state for the voice process. The loop, barge-in, the
dictation key and the overlay all read and move *this* object (``SESSION``); the microphone
coordinator (flow/mic.py) only says who holds the microphone, never what the conversation is
doing.

    WAKE_LISTENING ──wake──▶ ACTIVE_LISTENING ──speech──▶ ENDPOINTING ──▶ THINKING ──▶ ACTING
          ▲                        ▲    ▲                                    │           │
          │                        │    └──── INTERRUPTED ◀── barge-in ── SPEAKING ◀────┘
          │  "that's all" / quiet  │                                          │
          └──────── IDLE ◀──── FOLLOW_UP ◀────────────────────────────────────┘

    DICTATION   the dictation key took the microphone; the state it interrupted is resumed.
    SLEEPING    "go to sleep" — nothing but the wake phrase gets through.
    ERROR       something failed; ``recover`` puts the loop back in a known listening state.

"Stop" is not "that's all": stop cuts the current speech or action and the conversation stays
open; "that's all", "bas", "bye Jarvis" or a quiet follow-up window end it. Approvals are held by
the approval manager, not here, so they survive every transition in this file.

The voice loop used to ask for the wake word before every turn. "Jarvis, open YouTube" → "search
Pythagoras" → "play the first one" needs the second and third to work without the wake word, and
it needs a cough, a "hmm" or the TV not to become a fourth.

This module decides; the loop does the listening and speaking. It keeps no audio and does no I/O
beyond an optional state file, so every rule here is tested directly.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class State(str, Enum):
    IDLE = "idle"
    WAKE_LISTENING = "wake_listening"
    WAKE_DETECTED = "wake_detected"
    ACTIVE_LISTENING = "active_listening"
    ENDPOINTING = "endpointing"
    THINKING = "thinking"
    ACTING = "acting"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    FOLLOW_UP = "follow_up"
    FOLLOW_UP_WINDOW = "follow_up"          # the earlier name, kept for callers
    DICTATION = "dictation"
    SLEEPING = "sleeping"
    ERROR = "error"


# In a conversation: what is said next needs no wake word.
_IN_CONVERSATION = frozenset({State.ACTIVE_LISTENING, State.ENDPOINTING, State.THINKING,
                              State.ACTING, State.SPEAKING, State.INTERRUPTED, State.FOLLOW_UP,
                              State.WAKE_DETECTED})


ACT, IGNORE, END = "act", "ignore", "end"

# Said to close the conversation. Whole sentence only: "that's all I needed for the essay, now
# open Docs" is a request, not a goodbye.
# Lead-ins are allowed: "Yeah, that's it." and "Jarvis, that's it." were both heard, and both were
# answered as questions ("Just handling routine tasks.") instead of closing.
_END = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|thanks?|thank you|yeah|yes|yep|ya|haan|han|alright|all right|right|so|well|"
    r"hey|(?:hey\s+)?(?:jarvis|javis|jarvi|jarves|jars))[,.!\s]*)*(?:that'?s all|that'?s it|that will be all|"
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


# "Stop" — cut what is happening now, keep the conversation. Whole utterance only: "stop the
# timer" is a request about a timer.
_STOP = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|wait|jarvis|hey jarvis)[,\s]+)*(?:stop|stop it|stop talking|stop that|"
    r"wait|hold on|one (?:sec|second)|shh+|quiet|be quiet|shut up|ruko|ruk jao|ruk|chup|"
    r"chup karo|enough)[.!]?$")
# "Cancel everything" — stop speaking and cancel every cancellable job, running or queued.
_CANCEL_ALL = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|jarvis|hey jarvis)[,\s]+)*(?:cancel|stop|abort|kill) "
    r"(?:everything|all(?: of it| tasks| jobs)?|all that|every task)[.!]?$"
    r"|^sab (?:kuch )?(?:cancel|band|rok) (?:karo|kar do|do)[.!]?$")
# "Go on" — carry on from where an interrupted answer was cut off.
_CONTINUE = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|yes|haan|han)[,\s]+)?(?:continue|go on|carry on|keep going|go ahead|"
    r"please continue|continue please|aage bolo|aage batao|haan bolo|jaari rakho)[.!]?$")


def is_stop(text: str) -> bool:
    return bool(_STOP.match((text or "").strip()))


def is_cancel_all(text: str) -> bool:
    return bool(_CANCEL_ALL.match((text or "").strip()))


def is_continue(text: str) -> bool:
    return bool(_CONTINUE.match((text or "").strip()))


def state_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base) / "jarvis-conversation.json"


def read_published() -> dict:
    """The voice process's conversation state, for the other processes (web server, overlay)."""
    try:
        return json.loads(state_path().read_text())
    except Exception:  # noqa: BLE001
        return {"state": State.IDLE.value}


@dataclass
class ConversationSession:
    """The conversation: its state, how long to keep listening, and its short-lived memory."""

    window_s: float = 8.0          # silence allowed after a reply before the conversation ends
    max_turns: int = 20            # a runaway loop (the TV) ends eventually even if each turn passes
    clock: Callable[[], float] = time.monotonic
    state: State = State.IDLE
    turns: int = 0
    started_at: float = 0.0
    window_opened_at: float = 0.0
    expecting_answer: bool = False   # the last reply asked something: a one-word answer counts
    history: list[str] = field(default_factory=list)
    # Short-lived memory, cleared when the conversation ends. The backend's context (context.py)
    # resolves "it" and "the first one" for the handlers; this is what the voice loop itself
    # needs: what was cut off, which language the person is speaking, what was just done.
    interrupted_reply: str = ""
    language: str = ""
    last_action: str = ""
    error_kind: str = ""
    on_change: Optional[Callable[["ConversationSession"], None]] = None
    _resume: Optional[State] = None
    # Nobody said "Jarvis" before this utterance: it began in the follow-up window, or it is what
    # was said over Jarvis. Kept past ENDPOINTING, which the capture enters as soon as speech starts.
    _unaddressed: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ------------------------------------------------------------------ transitions
    def _go(self, state: State) -> State:
        with self._lock:
            self.state = state
            self.history.append(state.value)
            del self.history[:-50]
        if self.on_change is not None:
            try:
                self.on_change(self)
            except Exception:  # noqa: BLE001 — publishing must never break a transition
                pass
        return state

    def listen_for_wake(self) -> State:
        """Waiting for the wake word. Any conversation that was open is over."""
        if self.state is State.SLEEPING:
            return self.state
        self._forget()
        return self._go(State.WAKE_LISTENING)

    def wake(self) -> State:
        if self.state is State.SLEEPING:
            return self.state
        self.turns = 0
        self.started_at = self.clock()
        self._unaddressed = False
        self._go(State.WAKE_DETECTED)
        return self._go(State.ACTIVE_LISTENING)

    def listening(self) -> State:
        """Listening for the next request inside the conversation."""
        return self._go(State.ACTIVE_LISTENING)

    def endpointing(self) -> State:
        """Speech started; waiting for it to end."""
        if self.state is State.FOLLOW_UP:
            self._unaddressed = True
        return self._go(State.ENDPOINTING)

    def thinking(self) -> State:
        return self._go(State.THINKING)

    def acting(self, what: str = "") -> State:
        if what:
            self.last_action = what
        return self._go(State.ACTING)

    def speaking(self) -> State:
        return self._go(State.SPEAKING)

    def interrupted(self, unsaid: str = "") -> State:
        """The person talked over the reply: stop speaking and listen to them. What was not yet
        said is kept, so "go on" can finish it."""
        if unsaid.strip():
            self.interrupted_reply = unsaid.strip()
        self._unaddressed = True
        self._go(State.INTERRUPTED)
        return self._go(State.ACTIVE_LISTENING)

    def take_interrupted(self) -> str:
        said, self.interrupted_reply = self.interrupted_reply, ""
        return said

    def stopped(self) -> State:
        """"Stop": what was happening is cut, and the conversation carries on."""
        self.interrupted_reply = ""
        self.window_opened_at = self.clock()
        return self._go(State.FOLLOW_UP)

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
        self._unaddressed = True
        return self._go(State.FOLLOW_UP)

    def silence(self) -> State:
        """Nothing was said in the window."""
        return self.end()

    def window_expired(self) -> bool:
        return self.state is State.FOLLOW_UP and \
            self.clock() - self.window_opened_at >= self.window_s

    def end(self) -> State:
        self._forget()
        return self._go(State.IDLE)

    def _forget(self) -> None:
        self.interrupted_reply = ""
        self.language = ""
        self.last_action = ""
        self.expecting_answer = False
        self._resume = None

    def sleep(self) -> State:
        self._forget()
        return self._go(State.SLEEPING)

    def wake_from_sleep(self) -> State:
        return self._go(State.IDLE)

    # ------------------------------------------------------------------ dictation
    def dictation_started(self) -> State:
        """The dictation key took the microphone. Whatever the conversation was doing is
        remembered and picked up again afterwards."""
        with self._lock:
            if self.state is not State.DICTATION:
                self._resume = self.state
        return self._go(State.DICTATION)

    def dictation_ended(self) -> State:
        """Back to the conversation with a fresh follow-up window, or back to the wake word."""
        with self._lock:
            before, self._resume = self._resume, None
            if self.state is not State.DICTATION:
                return self.state
        if before in _IN_CONVERSATION:
            self.window_opened_at = self.clock()
            return self._go(State.FOLLOW_UP)
        if before is State.SLEEPING:
            return self._go(State.SLEEPING)
        return self._go(State.WAKE_LISTENING)

    # ------------------------------------------------------------------ failure
    def error(self, kind: str) -> State:
        """Something failed. Only the category is kept — never the words or the audio."""
        self.error_kind = kind
        return self._go(State.ERROR)

    def recover(self) -> State:
        """Back to a known listening state after an error or a timeout."""
        if self.state is State.SLEEPING:
            return self.state
        self.error_kind = ""
        return self.listen_for_wake()

    def snapshot(self) -> dict:
        return {"state": self.state.value, "turns": self.turns, "in_conversation": self.active,
                "interrupted": bool(self.interrupted_reply), "language": self.language,
                "at": time.time()}

    # ------------------------------------------------------------------ decisions
    @property
    def unaddressed(self) -> bool:
        """What is being judged was not preceded by the wake word."""
        if self.state is State.FOLLOW_UP:
            return True
        return self._unaddressed and self.state in {State.ENDPOINTING, State.ACTIVE_LISTENING,
                                                    State.INTERRUPTED}

    @property
    def active(self) -> bool:
        """In a conversation: what is said next needs no wake word."""
        return self.state in _IN_CONVERSATION

    def listen_for_s(self, default_after_wake: float) -> float:
        """How long the next capture should wait for speech to start."""
        return self.window_s if self.state is State.FOLLOW_UP else default_after_wake

    def judge(self, transcript: str) -> str:
        """ACT on it, IGNORE it and keep listening, or END the conversation.

        Right after the wake word everything is meant for Jarvis. In the follow-up window the
        person has not said the name, so only something that reads as a request counts.
        """
        said = (transcript or "").strip()
        words = re.findall(r"[\w']+", said.lower())
        if not said or not words or _PHANTOM.match(said):
            return IGNORE if self.unaddressed else ACT
        if _END.match(said):
            if self.expecting_answer and _ANSWER_NOT_GOODBYE.match(said):
                return ACT
            return END
        if not self.unaddressed or self.expecting_answer:
            return ACT
        if all(w in _FILLER for w in words):
            return IGNORE
        # Addressed by name again: certainly for us.
        if words[0] in {"jarvis", "hey"}:
            return ACT
        return ACT if len(words) >= 2 or words[0] in _ONE_WORD_COMMANDS else IGNORE


def publish_to_file(session: ConversationSession) -> None:
    """``on_change`` for the voice process: the state, for the web server and the overlay."""
    try:
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(session.snapshot()))
        tmp.replace(state_path())
    except OSError:
        pass


# The voice process's one conversation.
SESSION = ConversationSession()
