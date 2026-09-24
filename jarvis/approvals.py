"""One place where an action with consequences waits for a yes.

Before this there were two mechanisms and neither worked everywhere. Tools called a
``confirm_fn(description) -> bool`` that blocked for an answer — which only the terminal could
give; the web server, and therefore every voice turn, was built with ``confirm_fn=None``, so
e-mail and calendar tools answered "user declined" to requests nobody had declined. Messaging
had grown its own pending-send slot, which could hold exactly one thing.

Now every side effect that needs approval is *proposed* here and returns at once:

    propose(kind, summary, details, execute)  →  PendingAction(id, …), spoken "Ready to …"
    "yes" / "send it" / "haan bhej do"         →  confirm(id) runs ``execute`` — that action only
    "cancel" / "rehne do"                      →  cancel(id)

``execute`` is the original tool call, bound when it was proposed, so what runs after the yes is
exactly what was described before it. A yes that could mean two different pending actions
approves neither and asks which. Anything past its expiry cannot run. Every step is written to an
audit log with the kind, a masked target and a hash — never a message body, subject or command.

The terminal still answers inline: with an interactive ``ask`` callback, ``propose`` asks and
settles the action through the same confirm/cancel path, so the audit and the rules are the same.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

DEFAULT_TTL_S = 180.0

# Words that name a kind of action, so "send the email" or "cancel the message" can pick one.
KIND_WORDS: dict[str, tuple[str, ...]] = {
    "message": ("message", "whatsapp", "text", "msg", "sms"),
    "email": ("email", "e-mail", "mail", "gmail"),
    "calendar": ("calendar", "event", "meeting", "appointment", "invite"),
    "shell": ("command", "shell", "terminal", "script"),
    "file": ("file", "overwrite"),
    "browser": ("browser", "opera", "restart"),
    "autonomy": ("autonomy", "confirmation", "control"),
    "away": ("away", "away mode", "history"),
}

_YES = re.compile(
    r"(?ix)^(?:(?:yes|yeah|yep|haan|han|ha|ji|haan\s+ji|ok(?:ay)?|sure|theek\s+hai|thik\s+hai)[,\s]+)*"
    r"(?:yes|yeah|yep|haan(?:\s+ji)?|han|ji\s+haan|confirm(?:ed)?|approve(?:d)?|go\s+ahead|do\s+it|"
    r"send(?:\s+it)?|bhej(?:\s+do|\s+de|o)?|kar(?:\s+do|\s+de|o)|haan\s+kar(?:\s+do|\s+de)|haan\s+bhej(?:\s+do|\s+de)?|"
    r"theek\s+hai\s+kar\s+do|create\s+it|run\s+it|book\s+it)"
    r"(?P<rest>(?:\s+.*)?)$")
_NO = re.compile(
    r"(?ix)^(?:nhi\s+rehne\s+(?:de|do)|nahi\s+rehne\s+(?:do|de)|rehne\s+(?:do|de)|mat\s+(?:karo|bhejo|kar)|"
    r"no|nah|nope|nahi|nhi|na|mat|cancel(?:\s+it)?|don'?t(?:\s+send(?:\s+it)?|\s+do\s+it)?|do\s+not(?:\s+send(?:\s+it)?)?|"
    r"abort|stop|never\s*mind|chhodo|choddo)"
    r"(?P<rest>(?:\s+.*)?)$")
# Words that only ever answer a held action; everything else ("yes", "haan") can be conversation.
_APPROVAL_ONLY = re.compile(
    r"(?i)(?:(?:haan|han|yes|ok(?:ay)?)[,\s]+)?(?:send\s+it(?:\s+now)?|bhej\s+d[oe]|bhejo|confirm(?:ed)?|"
    r"haan\s+bhej\s+d[oe])")
# A bare "ok" is filler as often as it is consent; it approves nothing on its own.
_BARE_FILLER = re.compile(r"(?i)^(?:ok(?:ay)?|sure|theek hai|thik hai|achha|acha|hmm)$")
_COURTESY = {"it", "please", "the", "that", "this", "one", "ji", "do", "de", "karo", "yaar", "bhai",
             "now", "go", "ahead", "jarvis", "sir", "thanks", "thank", "you", "to", "a"}
_ALL_KIND_WORDS = {w for words in KIND_WORDS.values() for w in words}
_ORDINALS = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2,
             "last": -1, "latest": -1}


@dataclass
class PendingAction:
    id: str
    kind: str
    summary: str                       # what will happen, in words: 'send a WhatsApp to Papa'
    details: dict                      # recipient, platform, action — shown, hashed, never logged raw
    execute: Callable[[], Any]
    session: str = "local"
    created: float = field(default_factory=time.time)
    expires: float = 0.0

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires

    def prompt(self) -> str:
        return f'Ready to {self.summary}. Say "yes" to confirm or "cancel".'

    def fingerprint(self) -> str:
        blob = json.dumps({"kind": self.kind, **self.details}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def mentions(self) -> set[str]:
        words = set(KIND_WORDS.get(self.kind, ()))
        for key in ("recipient", "to", "title", "platform", "app"):
            value = str(self.details.get(key, "") or "").lower()
            words |= {w for w in re.findall(r"[\w@.]+", value) if len(w) > 1}
        return words


@dataclass
class Outcome:
    status: str                        # executed, failed, cancelled, expired, ambiguous, none
    message: str
    action: Optional[PendingAction] = None
    result: Any = None

    @property
    def ok(self) -> bool:
        return self.status == "executed"


def _state_dir() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()


def _mask(value: str) -> str:
    value = str(value or "")
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 7:
        return f"number ending {digits[-4:]}"
    if "@" in value and "." in value.split("@")[-1]:
        user, domain = value.split("@", 1)
        return f"{user[:1]}…@{domain}"
    return value[:40]


class ApprovalManager:
    def __init__(self, ttl_s: float = DEFAULT_TTL_S, audit_path: Optional[Path] = None) -> None:
        self.ttl_s = ttl_s
        self._pending: dict[str, PendingAction] = {}
        self._expired: dict[str, PendingAction] = {}   # kept a while, to say "that expired"
        self._audit_path = audit_path

    # ------------------------------------------------------------------ audit
    def _audit(self, event: str, action: PendingAction, note: str = "") -> None:
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, "id": action.id,
               "kind": action.kind, "session": action.session, "fingerprint": action.fingerprint(),
               "target": _mask(action.details.get("recipient") or action.details.get("to") or "")}
        if note:
            row["note"] = re.sub(r"\b\d{7,}\b", "<number>", note)[:200]
        path = self._audit_path or (_state_dir() / "approvals.jsonl")
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.chmod(path, 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------------ lifecycle
    def _sweep(self) -> None:
        for action in [a for a in self._pending.values() if a.expired]:
            self._pending.pop(action.id, None)
            self._expired[action.id] = action
            self._audit("expired", action)
        for action in [a for a in self._expired.values() if time.time() - a.expires > 600]:
            self._expired.pop(action.id, None)

    def propose(self, kind: str, summary: str, details: dict, execute: Callable[[], Any], *,
                session: str = "local", ttl_s: Optional[float] = None) -> PendingAction:
        """Hold an action until it is approved. Proposing the same thing twice replaces the first."""
        self._sweep()
        action = PendingAction(id=secrets.token_hex(3), kind=kind, summary=summary, details=dict(details),
                               execute=execute, session=session)
        action.expires = action.created + (self.ttl_s if ttl_s is None else ttl_s)
        for other in [a for a in self._pending.values()
                      if a.session == session and a.fingerprint() == action.fingerprint()]:
            self._pending.pop(other.id, None)
            self._audit("superseded", other)
        self._pending[action.id] = action
        self._audit("proposed", action)
        return action

    def pending(self, session: Optional[str] = None) -> list[PendingAction]:
        self._sweep()
        return [a for a in sorted(self._pending.values(), key=lambda a: a.created)
                if session is None or a.session == session]

    def get(self, action_id: str) -> Optional[PendingAction]:
        return self._pending.get(action_id)

    async def confirm(self, action_id: str, *, fingerprint: Optional[str] = None) -> Outcome:
        """Run exactly this action. ``fingerprint``, when given, must match what was proposed."""
        action = self._pending.get(action_id)
        if action is None and action_id in self._expired:
            action = self._expired[action_id]
        if action is None:
            return Outcome("none", "There's nothing waiting for approval with that id.")
        if action.expired:
            if self._pending.pop(action_id, None) is not None:
                self._audit("expired", action)
            return Outcome("expired", f"That request to {action.summary} expired, so I didn't do it. "
                                      "Ask again if you still want it.", action)
        if fingerprint is not None and fingerprint != action.fingerprint():
            self._audit("refused", action, "fingerprint mismatch")
            return Outcome("none", "That approval was for a different action, so I didn't run it.", action)
        self._pending.pop(action_id, None)
        self._audit("confirmed", action)
        try:
            result = action.execute()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 — the provider's own words are the report
            self._audit("failed", action, f"{type(exc).__name__}: {exc}")
            return Outcome("failed", f"I tried to {action.summary}, but it failed: {exc}", action)
        ok, message = _read_result(result)
        self._audit("executed" if ok else "failed", action, "" if ok else message)
        if ok:
            return Outcome("executed", message or "Done.", action, result)
        return Outcome("failed", message or f"I couldn't {action.summary}.", action, result)

    def cancel(self, action_id: str) -> Outcome:
        action = self._pending.pop(action_id, None)
        if action is None:
            return Outcome("none", "Nothing was waiting.")
        self._audit("cancelled", action)
        return Outcome("cancelled", f"Cancelled — I won't {action.summary}.", action)

    def clear(self) -> None:
        self._pending.clear()
        self._expired.clear()

    # ------------------------------------------------------------------ speech
    def _choose(self, rest: str, candidates: list[PendingAction]) -> tuple[Optional[PendingAction], str]:
        """Which pending action the words after "yes"/"no" pick.

        Returns (action, how): "picked" one; "plain" — nothing but courtesy, so the caller decides;
        "refers" — it names an action, but not exactly one pending one; "unrelated" — the rest is
        a sentence of its own ("stop reading notifications"), so this was never an answer.
        """
        words = set(re.findall(r"[\w@.]+", rest.lower())) - _COURTESY
        if not words:
            return None, "plain"
        for word, index in _ORDINALS.items():
            if word in words:
                try:
                    return candidates[index], "picked"
                except IndexError:
                    return None, "refers"
        named = [a for a in candidates if words & a.mentions()]
        if len(named) == 1:
            return named[0], "picked"
        if named or words & _ALL_KIND_WORDS:
            return None, "refers"
        return None, "unrelated"

    async def answer(self, text: str, session: str = "local") -> Optional[Outcome]:
        """Settle a pending action from what was said, or None if this was not an answer."""
        waiting = self.pending(session)
        if not waiting:
            # A yes to something that has just expired must say so, not fall through to the
            # model as if it were a new request.
            lapsed = [a for a in self._expired.values() if a.session == session]
            said = re.sub(r"(?i)^(?:hey\s+)?jarvis[,.!\s]*", "", (text or "").strip()).rstrip(".!?").strip()
            m = _YES.match(said)
            if lapsed and m and not _BARE_FILLER.fullmatch(said) and not (m.group("rest") or "").strip(" ,"):
                action = max(lapsed, key=lambda a: a.expires)
                self._expired.pop(action.id, None)
                return Outcome("expired", f"That request to {action.summary} expired, so I didn't do "
                                          "it. Ask again if you still want it.", action)
            # "Send it" with nothing held used to reach the model, which answered "Yes." — as
            # if something had been sent. A bare "yes" or "haan" stays conversation: it is as
            # often the answer to a question the model asked.
            if _APPROVAL_ONLY.fullmatch(said):
                return Outcome("none", "There's nothing waiting to be sent or confirmed right now.")
            return None
        said = re.sub(r"(?i)^(?:hey\s+)?jarvis[,.!\s]*", "", (text or "").strip()).rstrip(".!?").strip()
        if not said or _BARE_FILLER.fullmatch(said):
            return None
        if _is_a_new_request(said):
            # "Send 'Bye' to this number on WhatsApp" starts with "send" and names WhatsApp, and
            # was read as "yes, send the held one". A sentence that carries its own text or
            # recipient is a new request, never an answer.
            return None
        m_yes, m_no = _YES.match(said), _NO.match(said)
        if not (m_yes or m_no):
            # Found live: "cancel", misheard as "Gantel", went to the model while a WhatsApp
            # message waited for a yes. A short reply that sounds like "cancel" cancels — fuzzy
            # matching is never used to say yes — and any other short reply is asked about
            # again rather than handed to a model beside a message waiting to be sent.
            words = re.findall(r"[\w']+", said.lower())
            command = words and words[0] in {
                "open", "play", "pause", "search", "find", "stop", "close", "show", "what", "who",
                "how", "why", "when", "where", "explain", "turn", "set", "call", "message", "tell",
                "read", "go", "next", "resume", "mute", "volume", "take", "start"}
            if 0 < len(words) <= 3 and not command:
                import difflib
                if any(difflib.SequenceMatcher(None, w, target).ratio() >= 0.6
                       for w in words for target in ("cancel", "cancelled")):
                    return self.cancel(waiting[-1].id)
                return Outcome("unclear", "I didn't catch that — say yes to go ahead, or cancel. "
                                          "Nothing has been done yet.")
            return None
        yes = bool(m_yes) and not (m_no and not said.lower().startswith(("yes", "haan", "han", "send", "confirm")))
        match = m_yes if yes else m_no
        rest = (match.group("rest") or "").strip(" ,")
        chosen, how = self._choose(rest, waiting)
        if how == "unrelated":
            return None
        if chosen is None:
            if how == "refers" or len(waiting) > 1:
                options = " or ".join(f"the {a.kind} ({a.summary})" for a in waiting[:3])
                return Outcome("ambiguous", f"Which one — {options}? I haven't done anything yet.")
            chosen = waiting[0]
        return await self.confirm(chosen.id) if yes else self.cancel(chosen.id)

    async def propose_or_ask(self, kind: str, summary: str, details: dict, execute: Callable[[], Any], *,
                             ask: Optional[Callable[[str], Awaitable[bool]]] = None,
                             session: str = "local") -> Outcome:
        """Propose; with an interactive ``ask`` (the terminal), settle it right away."""
        action = self.propose(kind, summary, details, execute, session=session)
        if ask is None:
            return Outcome("pending", action.prompt(), action)
        approved = await ask(summary)
        return await self.confirm(action.id) if approved else self.cancel(action.id)


def _is_a_new_request(said: str) -> bool:
    if re.search(r"[\"“‘]|(?:^|\s)'\S", said):
        return True
    try:
        from .message_command import read
        req = read(said)
    except Exception:  # noqa: BLE001 — a parser failure must not approve anything either way
        return False
    return req is not None and bool(req.body or req.number)


def _read_result(result: Any) -> tuple[bool, str]:
    """(succeeded, message) from what a tool returned: {ok, message} dicts, strings, or None."""
    if isinstance(result, dict):
        ok = bool(result.get("ok", result.get("status") in {"sent", "executed", "created"}))
        return ok, str(result.get("message") or result.get("error") or "")
    if result is None:
        return False, "The service isn't connected."
    text = str(result)
    failed = re.match(r"(?i)^\s*(?:error|failed|couldn'?t|could not|tool error|\[error\])", text)
    return not failed, text


MANAGER = ApprovalManager()


def run_sync(coro):
    """Run an Outcome coroutine from sync code (tests, threads)."""
    return asyncio.run(coro)
