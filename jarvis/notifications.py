"""What a phone notification becomes when it is said out loud.

Before this, every message was read the moment it arrived as "<app> message from <title>.
<text>." Seven Instagram messages from one person were seven announcements. A sender that was a
phone number written with spaces was read digit by digit, because the check for "is this a
number" was ``isdigit()``. Links, order IDs and tracking codes in the text were read too.

The pipeline, in order:

    normalise    who sent it, in words a person would say; what it says, minus links and IDs
    dedupe       the same message twice (KDE Connect and the bridge both saw it) is one message
    policy       interrupt now, announce, keep for the summary, or drop — from the owner's rules
    group        a burst from one conversation waits a few seconds and is said once

Everything here is pure or keeps its own small state, so it is tested without audio. The voice
session feeds events in and speaks whatever comes out.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- normalisation

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿️‍]")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
# Something that is plainly a code rather than a word: long, and mixes digits with letters, or
# is a long run of digits. Order numbers, OTP references, tracking IDs.
_CODE = re.compile(r"\b(?=[A-Za-z0-9_-]{10,}\b)(?=[A-Za-z_-]*\d)[A-Za-z0-9_-]+\b|\b\d{7,}\b")
_PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")
# Wrappers apps put around the sender: "WhatsApp: ", "Instagram · ", "(3 messages)", "~".
_TITLE_NOISE = re.compile(
    r"(?i)^(?:whatsapp|instagram|telegram|messenger|signal|messages|sms)\s*[:·•|-]\s*"
    r"|\s*\(\d+\s+(?:new\s+)?messages?\)\s*$|\s*[:·•]\s*\d+\s+new messages?$|^~\s*|\s*:\s*$")
# "Papa @ Family Group": the sender inside a group title.
_GROUP_SENDER = re.compile(r"^(?P<who>.+?)\s*@\s*(?P<group>.+)$")

NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "eleven", "twelve"]


def count_words(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def _name_for_number(raw: str) -> str:
    """The saved name for a phone number, or ""."""
    try:
        from .integrations import contacts, phone_contacts
        want = contacts.dialable(raw)
        if not want:
            return ""
        for saved in contacts.all_contacts():
            if saved.get("number") and contacts.dialable(saved["number"]) == want:
                return saved["name"]
        for c in phone_contacts.all_contacts():
            if contacts.dialable(c["number"]) == want:
                return c["name"]
    except Exception:  # noqa: BLE001 — no address book means no name, not an error
        pass
    return ""


def _username_words(handle: str) -> str:
    """"arjun.chandra_07" → "arjun chandra"; "the_real_rohit_k" → "the real rohit"."""
    words = [w for w in re.split(r"[._\-\s]+", handle) if w]
    words = [re.sub(r"\d+$", "", w) for w in words]
    words = [w for w in words if len(w) > 1 and not w.isdigit()]
    return " ".join(words[:3])


def speakable_sender(raw: str, lookup=_name_for_number) -> str:
    """A sender as a person would say it. Never a phone number, a JID, or a long handle."""
    title = _EMOJI.sub("", str(raw or "")).strip()
    for _ in range(3):
        cleaned = _TITLE_NOISE.sub("", title).strip()
        if cleaned == title:
            break
        title = cleaned
    if not title:
        return "someone"
    if "@" in title and re.match(r"^[\d+]+@", title):          # a JID
        title = title.split("@", 1)[0]
    if _PHONE.match(title):
        name = lookup(title)
        return name or "an unknown number"
    m = _GROUP_SENDER.match(title)
    if m and "." not in m.group("who"):
        title = m.group("who").strip()
    if re.fullmatch(r"@?[\w.]+", title) and re.search(r"[._\d]", title):
        return _username_words(title.lstrip("@")) or "someone"
    words = title.split()
    return " ".join(words[:4])


def speakable_text(text: str, max_words: int = 24) -> str:
    """Message text fit for reading aloud: no links, codes or emoji, and not a paragraph."""
    t = _EMOJI.sub("", str(text or ""))
    t = _URL.sub(" a link ", t)
    t = _CODE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,-")
    t = re.sub(r"(?:\ba link\b[\s,.]*){2,}", "some links ", t).strip()
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words]) + " …"
    return t


# --------------------------------------------------------------------------- events

@dataclass
class NotificationEvent:
    app: str
    sender: str                  # as the app gave it
    text: str
    thread: str = ""             # conversation / chat id when the source has one
    ts: float = field(default_factory=time.time)
    is_call: bool = False

    @property
    def who(self) -> str:
        return speakable_sender(self.sender)

    @property
    def conversation(self) -> str:
        return f"{self.app.lower()}|{self.thread or self.who.lower()}"


URGENT, ANNOUNCE, SUMMARY, SUPPRESS = "urgent", "announce", "summary", "suppress"

_URGENT_WORDS = re.compile(
    r"(?i)\b(?:urgent|emergency|asap|call me|call back|accident|hospital|help|jaldi|turant|"
    r"abhi call|phone uthao|pick up)\b")


# --------------------------------------------------------------------------- the owner's rules

def _rules_path() -> Path:
    from .preferences import state_dir
    return state_dir() / "notification-rules.json"


def load_rules() -> dict:
    try:
        data = json.loads(_rules_path().read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_rules(rules: dict) -> None:
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".notification-rules-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(rules, f, indent=2, ensure_ascii=False)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _active(until) -> bool:
    return until is None or until == "forever" or float(until) > time.time()


def mute(kind: str, name: str, seconds: float | None = None) -> None:
    """Mute an app, a sender, or a thread; ``seconds=None`` means until unmuted."""
    rules = load_rules()
    bucket = rules.setdefault(f"muted_{kind}s", {})
    bucket[name.lower()] = "forever" if seconds is None else time.time() + seconds
    save_rules(rules)


def unmute(kind: str, name: str) -> bool:
    rules = load_rules()
    removed = rules.get(f"muted_{kind}s", {}).pop(name.lower(), None) is not None
    save_rules(rules)
    return removed


def set_only_family(on: bool) -> None:
    rules = load_rules()
    rules["only_family"] = bool(on)
    save_rules(rules)


_FAMILY_WORDS = {"papa", "mummy", "mumma", "mom", "dad", "daddy", "maa", "bhai", "bhaiya", "didi",
                 "dadi", "dadu", "nani", "nana", "chacha", "chachi", "mama", "mami", "bua", "masi"}


def is_family(who: str) -> bool:
    # The name *is* the relationship ("Papa", "Mummy Ranchi"), not merely mentions one:
    # "Kabir Dad" is a classmate's father.
    words = who.lower().split()
    if words and words[0] in _FAMILY_WORDS and len(words) <= 2:
        return True
    try:
        from .integrations import contacts
        saved = contacts.lookup(who)
        return bool(saved and saved.get("relationship"))
    except Exception:  # noqa: BLE001
        return False


def classify(event: NotificationEvent, *, busy: bool = False, rules: dict | None = None) -> str:
    """Urgent, announce, summary or suppress. ``busy``: a call, a meeting, media, or focus."""
    rules = load_rules() if rules is None else rules
    who = event.who.lower()
    for kind, key in (("app", event.app.lower()), ("sender", who), ("thread", event.thread.lower())):
        until = rules.get(f"muted_{kind}s", {}).get(key)
        if key and until is not None and _active(until):
            return SUMMARY
    urgent = bool(_URGENT_WORDS.search(event.text or "")) or event.is_call
    if rules.get("only_family") and not is_family(event.who):
        return URGENT if urgent and event.is_call else SUMMARY
    if urgent:
        return URGENT
    return SUMMARY if busy else ANNOUNCE


# --------------------------------------------------------------------------- grouping

@dataclass
class Announcement:
    text: str
    priority: str
    events: list[NotificationEvent]


def _phrase(events: list[NotificationEvent]) -> str:
    app = events[0].app
    senders: list[str] = []
    for e in events:
        if e.who not in senders:
            senders.append(e.who)
    n = len(events)
    latest = speakable_text(events[-1].text)
    if n == 1:
        return f"{app} from {senders[0]}: {latest}" if latest else f"A {app} message from {senders[0]}."
    if len(senders) == 1:
        head = f"{count_words(n).capitalize()} new {app} messages from {senders[0]}."
    else:
        shown = senders[:2]
        rest = len(senders) - len(shown)
        who = " and ".join(shown) if not rest else f"{', '.join(shown)} and {count_words(rest)} " \
                                                     f"other{'s' if rest > 1 else ''}"
        head = f"{count_words(n).capitalize()} new {app} messages from {who}."
    return f"{head} The latest says: {latest}" if latest else head


def _shared_path() -> Path:
    from .preferences import state_dir
    return state_dir() / "notification-digest.json"


def _load_shared() -> dict:
    try:
        data = json.loads(_shared_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_shared(data: dict) -> None:
    path = _shared_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass


class Aggregator:
    """Holds a conversation's messages until it goes quiet, then says them once.

    ``quiet_s``: how long a conversation must be silent before it is announced. ``max_wait_s``:
    the longest anything waits, so a chat that never stops still gets heard.
    """

    def __init__(self, quiet_s: float = 5.0, max_wait_s: float = 20.0, dedupe_s: float = 600.0,
                 clock=time.monotonic, shared: bool = False) -> None:
        self.quiet_s, self.max_wait_s, self.dedupe_s = quiet_s, max_wait_s, dedupe_s
        self.shared = shared                       # mirror digest + last announcement to disk
        self.clock = clock
        self._open: dict[str, dict] = {}          # conversation -> {events, first, last}
        self._seen: dict[tuple, float] = {}
        self.digest: list[NotificationEvent] = []  # held for "summarise my notifications"
        self.last_said: str = ""

    def add(self, event: NotificationEvent, *, busy: bool = False, rules: dict | None = None) -> str:
        """Take one event; returns how it was classified. Urgent ones are returned by ``due`` at once."""
        now = self.clock()
        key = (event.app.lower(), event.who.lower(), (event.text or "").strip().lower())
        self._seen = {k: t for k, t in self._seen.items() if now - t < self.dedupe_s}
        if key in self._seen:
            return SUPPRESS
        self._seen[key] = now
        verdict = classify(event, busy=busy, rules=rules)
        if verdict == SUMMARY:
            self.digest.append(event)
            del self.digest[:-200]
            if self.shared:
                data = _load_shared()
                held = data.get("digest", []) + [{"app": event.app, "who": event.who,
                                                  "text": speakable_text(event.text)}]
                data["digest"] = held[-200:]
                _save_shared(data)
            return verdict
        slot = self._open.setdefault(event.conversation,
                                     {"events": [], "first": now, "last": now, "urgent": False})
        slot["events"].append(event)
        slot["last"] = now
        slot["urgent"] = slot["urgent"] or verdict == URGENT
        return verdict

    def due(self) -> list[Announcement]:
        """Announcements whose conversations have gone quiet (or are urgent, or waited too long)."""
        now = self.clock()
        out = []
        for conv, slot in list(self._open.items()):
            quiet = now - slot["last"] >= self.quiet_s
            waited = now - slot["first"] >= self.max_wait_s
            if slot["urgent"] or quiet or waited:
                del self._open[conv]
                text = _phrase(slot["events"])
                out.append(Announcement(text, URGENT if slot["urgent"] else ANNOUNCE, slot["events"]))
        if out:
            self.last_said = " ".join(a.text for a in out)
            if self.shared:
                data = _load_shared()
                last = out[-1].events[-1]
                data.update(last_said=self.last_said, last_app=last.app, last_who=last.who,
                            last_thread=last.thread)
                _save_shared(data)
        return out

    def summary(self, clear: bool = True) -> str:
        """Everything held back, grouped by app and person, said in a sentence or three."""
        held = [(e.app, e.who) for e in self.digest]
        if clear:
            self.digest.clear()
        return summarise(held)


def shared_summary(clear: bool = True) -> str:
    """The summary from any process: the voice session keeps the digest on disk."""
    data = _load_shared()
    held = [(d.get("app", "phone"), d.get("who", "someone")) for d in data.get("digest", [])]
    if clear and held:
        data["digest"] = []
        _save_shared(data)
    return summarise(held)


def last_announcement() -> dict:
    data = _load_shared()
    return {k: data.get(k, "") for k in ("last_said", "last_app", "last_who", "last_thread")}


def summarise(held: list[tuple[str, str]]) -> str:
    if not held:
        return "Nothing new while you were busy."
    by_app: dict[str, dict[str, int]] = {}
    for app, who in held:
        by_app.setdefault(app, {}).setdefault(who, 0)
        by_app[app][who] += 1
    parts = []
    for app, people in by_app.items():
        total = sum(people.values())
        top = sorted(people.items(), key=lambda kv: -kv[1])
        names = [f"{w} ({c})" if c > 1 else w for w, c in top[:3]]
        more = len(top) - 3
        tail = f" and {count_words(more)} more" if more > 0 else ""
        parts.append(f"{count_words(total).capitalize()} on {app}, from {', '.join(names)}{tail}.")
    return " ".join(parts)


# --------------------------------------------------------------------------- calls

def call_announcement(name: str, number: str, event: str) -> str:
    who = speakable_sender(name or number or "")
    if who == "someone":
        who = "an unknown number"
    if "missed" in (event or "").lower():
        return f"You missed a call from {who}."
    return f"Incoming call from {who}."
