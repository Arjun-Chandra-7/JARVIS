"""Phone calls during away mode — what is real, and what is ready for when it can be.

Answering a call for the owner needs six things at once: pick the call up, hear the caller, be
heard by the caller, notice the hang-up, know who is calling, and do all of it quickly enough to
hold a conversation. ``CallCapabilities`` names them, and ``CallHandler`` converses only when an
adapter reports every one.

On this machine the phone is reached through KDE Connect, whose telephony plugin reports only
``callReceived(event, number, name)``: ringing, talking, missed — caller identity, nothing else.
It cannot answer, cannot carry audio either way. So with KDE Connect calls are *recorded*, repeated
calls from family or VIPs escalate, and (only if the owner chose it) the caller is texted. The
full conversation path runs against ``SimulatedCallAdapter`` until a real bridge exists — an
Android InCallService/ConnectionService companion, a Bluetooth HFP audio gateway, or a SIP trunk
with call forwarding would each fill the interface.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import policy, replies
from .language import reply_language

MAX_CALL_S = 180.0
LISTEN_TIMEOUT_S = 8.0
MAX_TURNS = 6
_RANK = {"normal": 0, "important": 1, "urgent": 2, "emergency": 3}


@dataclass(frozen=True)
class CallCapabilities:
    answer: bool = False
    caller_audio: bool = False
    send_audio: bool = False
    hangup_events: bool = False
    caller_id: bool = False
    low_latency: bool = False

    @property
    def can_converse(self) -> bool:
        return all((self.answer, self.caller_audio, self.send_audio, self.hangup_events, self.caller_id, self.low_latency))

    def missing(self) -> list[str]:
        names = {"answer": "answering the call", "caller_audio": "hearing the caller",
                 "send_audio": "speaking to the caller", "hangup_events": "detecting hang-up",
                 "caller_id": "caller identity", "low_latency": "low-latency audio"}
        return [text for key, text in names.items() if not getattr(self, key)]


@dataclass
class IncomingCall:
    call_id: str
    number: str
    name: str
    started: float = field(default_factory=time.time)


class CallAdapter:
    """What a phone bridge must provide. Every method but ``capabilities`` may raise."""

    name = "none"

    def capabilities(self) -> CallCapabilities:
        return CallCapabilities()

    async def answer(self, call: IncomingCall) -> bool:
        raise NotImplementedError

    async def speak(self, call: IncomingCall, text: str) -> bool:
        """Play text to the caller. Returns False if the caller interrupted (barge-in)."""
        raise NotImplementedError

    async def listen(self, call: IncomingCall, timeout: float) -> Optional[str]:
        """The caller's next utterance as text; None on silence; raises CallEnded on hang-up."""
        raise NotImplementedError

    async def hangup(self, call: IncomingCall) -> None:
        raise NotImplementedError

    def connected(self, call: IncomingCall) -> bool:
        return False


class CallEnded(Exception):
    pass


class KDEConnectCallMonitor(CallAdapter):
    """The real bridge here: ring and missed-call events with the caller's name and number."""

    name = "kdeconnect"

    def capabilities(self) -> CallCapabilities:
        return CallCapabilities(caller_id=True)


class SimulatedCallAdapter(CallAdapter):
    """A scripted caller, for tests and dry runs. Behaves like a full bridge would.

    ``script`` is what the caller says, turn by turn; ``"<hangup>"`` ends the call and
    ``("<barge>", text)`` interrupts Jarvis mid-sentence with ``text``.
    """

    name = "simulator"

    def __init__(self, script: list[Any], *, latency_s: float = 0.0, caps: Optional[CallCapabilities] = None) -> None:
        self.script = list(script)
        self.latency_s = latency_s
        self.spoken: list[str] = []
        self.answered = False
        self.hung_up = False
        self._barge: Optional[str] = None
        self._caps = caps or CallCapabilities(True, True, True, True, True, True)

    def capabilities(self) -> CallCapabilities:
        return self._caps

    async def answer(self, call: IncomingCall) -> bool:
        self.answered = True
        return True

    async def speak(self, call: IncomingCall, text: str) -> bool:
        if self.hung_up:
            raise CallEnded()
        self.spoken.append(text)
        if self.script and isinstance(self.script[0], tuple) and self.script[0][0] == "<barge>":
            self._barge = self.script.pop(0)[1]
            return False
        return True

    async def listen(self, call: IncomingCall, timeout: float) -> Optional[str]:
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self._barge is not None:
            said, self._barge = self._barge, None
            return said
        if not self.script:
            return None
        said = self.script.pop(0)
        if said == "<hangup>":
            self.hung_up = True
            raise CallEnded()
        return said

    async def hangup(self, call: IncomingCall) -> None:
        self.hung_up = True

    def connected(self, call: IncomingCall) -> bool:
        return self.answered and not self.hung_up


@dataclass
class CallOutcome:
    answered: bool
    turns: int = 0
    duration_s: float = 0.0
    ended_by: str = ""                 # caller | limit | owner | silence | not_supported | policy
    urgency: str = "normal"
    notes: list[str] = field(default_factory=list)       # redacted gists
    restricted: list[str] = field(default_factory=list)
    interrupted: int = 0
    reason: str = ""


class CallHandler:
    """Take a message by voice. Never records audio: only redacted text notes are kept."""

    def __init__(self, adapter: CallAdapter, *, owner: str, return_time: str = "", languages: tuple[str, ...] = ("en", "hinglish", "hi"),
                 max_call_s: float = MAX_CALL_S, clock=time.monotonic) -> None:
        self.adapter = adapter
        self.owner = owner
        self.return_time = return_time
        self.languages = list(languages)
        self.max_call_s = max_call_s
        self.clock = clock
        self._takeover = asyncio.Event()
        self._hangup_now = asyncio.Event()

    def owner_takeover(self) -> None:
        self._takeover.set()

    def hang_up_now(self) -> None:
        self._hangup_now.set()

    async def handle(self, call: IncomingCall, *, policy_allows_answer: bool) -> CallOutcome:
        caps = self.adapter.capabilities()
        if not caps.can_converse:
            return CallOutcome(False, ended_by="not_supported",
                               reason="can't answer calls: missing " + ", ".join(caps.missing()))
        if not policy_allows_answer:
            return CallOutcome(False, ended_by="policy", reason="call answering is off for this away session")
        out = CallOutcome(answered=await self.adapter.answer(call))
        if not out.answered:
            out.ended_by, out.reason = "not_supported", "the phone did not pick up"
            return out
        start = self.clock()
        lang = "en"
        try:
            if not await self.adapter.speak(call, replies.call_disclosure(self.owner, lang)):
                out.interrupted += 1
            silence = 0
            while True:
                if self._takeover.is_set():
                    out.ended_by = "owner"
                    return out          # the owner has the line; Jarvis stops speaking at once
                if self._hangup_now.is_set():
                    await self.adapter.hangup(call)
                    out.ended_by = "owner"
                    return out
                if self.clock() - start >= self.max_call_s or out.turns >= MAX_TURNS:
                    await self.adapter.speak(call, replies.wrap_up(self.owner, lang))
                    await self.adapter.hangup(call)
                    out.ended_by = "limit"
                    return out
                heard = await self.adapter.listen(call, LISTEN_TIMEOUT_S)
                if heard is None:
                    silence += 1
                    if silence >= 2:
                        await self.adapter.hangup(call)
                        out.ended_by = "silence"
                        return out
                    continue
                silence = 0
                out.turns += 1
                c = policy.classify(heard)
                lang = reply_language(c.language, self.languages)
                if _RANK[c.urgency] > _RANK[out.urgency]:
                    out.urgency = c.urgency
                out.restricted = sorted(set(out.restricted) | set(c.restricted))
                if not c.is_restricted:
                    out.notes.append(policy.redact(heard, 140))
                answer = self._answer(c, lang)
                if not await self.adapter.speak(call, answer):
                    out.interrupted += 1
                if "closing" in c.tags:
                    await self.adapter.hangup(call)
                    out.ended_by = "caller"
                    return out
        except CallEnded:
            out.ended_by = out.ended_by or "caller"
            return out
        finally:
            out.duration_s = self.clock() - start

    def _answer(self, c: policy.Classification, lang: str) -> str:
        if c.injection:
            return replies.decline(self.owner, ["policy_change"], lang)
        if c.urgency == "emergency":
            return replies.emergency_ack(self.owner, lang)
        if c.is_restricted:
            return replies.decline(self.owner, c.restricted, lang)
        if c.urgency == "urgent":
            return replies.urgent_ack(self.owner, lang)
        if "closing" in c.tags:
            return replies.closing(self.owner, lang)
        if "return_question" in c.tags:
            return replies.return_time(self.owner, self.return_time, lang)
        if "call_request" in c.tags:
            return replies.call_back(self.owner, lang)
        return replies.noted(self.owner, lang) + {"en": " Anything else?", "hinglish": " Aur kuch?", "hi": " और कुछ?"}.get(lang, "")


def capability_report(adapter: Optional[CallAdapter] = None) -> dict[str, Any]:
    adapter = adapter or KDEConnectCallMonitor()
    caps = adapter.capabilities()
    return {"adapter": adapter.name, "can_answer": caps.can_converse, "missing": caps.missing(),
            "real": ["caller identity", "ringing and missed-call events", "text-back by SMS (unverified)"]
            if adapter.name == "kdeconnect" else []}


def capability_sentence(adapter: Optional[CallAdapter] = None) -> str:
    rep = capability_report(adapter)
    if rep["can_answer"]:
        return "I can answer calls and take a message."
    return ("I can't answer calls: the phone link (KDE Connect) only tells me who is calling. "
            "I'll note each call and alert you if someone keeps calling.")
