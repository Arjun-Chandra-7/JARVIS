"""Who sent a message, in terms of the owner's own contacts.

Reuses the canonical store (``integrations/contacts.py``: names, numbers, aliases, relationship).
A number is matched on its last ten digits, a name exactly, never fuzzily — away mode deciding that
a stranger is "Papa" would give a stranger family treatment. Unmatched senders get a stable,
hashed id, so their thread and briefing line hold together without keeping their number in it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

_FAMILY_RELATIONS = {"father", "mother", "brother", "sister", "grandmother", "grandfather", "family",
                     "uncle", "aunt", "wife", "husband", "son", "daughter", "cousin", "parent"}


@dataclass
class Sender:
    contact_id: str
    name: str
    family: bool = False
    vip: bool = False
    known: bool = False
    aliases: list[str] = field(default_factory=list)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", (value or "").split("@", 1)[0])


def _speakable(name: str) -> str:
    try:
        from ..notifications import speakable_sender
        return speakable_sender(name)
    except Exception:  # noqa: BLE001
        return name


def contact_key(name: str) -> str:
    return "contact:" + re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


class Resolver:
    def __init__(self, entries: Optional[Iterable[dict]] = None, vip: Iterable[str] = ()) -> None:
        self._entries = list(entries) if entries is not None else None
        self.vip = {v.lower() for v in vip}

    def entries(self) -> list[dict]:
        if self._entries is not None:
            return self._entries
        try:
            from ..integrations import contacts
            return contacts.all_contacts()
        except Exception:  # noqa: BLE001
            return []

    def find(self, name_or_alias: str) -> Optional[dict]:
        q = (name_or_alias or "").strip().lower()
        if not q:
            return None
        for e in self.entries():
            if q == str(e.get("name", "")).lower() or q in {str(a).lower() for a in e.get("aliases", [])}:
                return e
        return None

    def resolve(self, platform: str, sender_id: str, display: str) -> Sender:
        digits = _digits(sender_id)
        match = None
        if len(digits) >= 7:
            for e in self.entries():
                d = _digits(str(e.get("number", "")))
                if d and d[-10:] == digits[-10:]:
                    match = e
                    break
        if match is None and display and not re.fullmatch(r"[+\d\s()-]+", display):
            match = self.find(display)
        if match is not None:
            name = str(match.get("name"))
            relation = str(match.get("relationship", "")).lower()
            cid = contact_key(name)
            aliases = [str(a) for a in match.get("aliases", [])]
            family = relation in _FAMILY_RELATIONS or self._family_word(name, aliases)
            vip = self._is_vip(cid, [name, *aliases])
            return Sender(cid, name, family=family, vip=vip, known=True, aliases=aliases)
        shown = _speakable(display) if display and not re.fullmatch(r"[+\d\s()@.a-z-]*\d{6,}[^ ]*", display) else ""
        cid = f"{platform}:" + hashlib.sha256((sender_id or display).encode()).hexdigest()[:12]
        family = bool(shown) and self._family_word(shown, [])
        # A name the bridge learned from the owner's address book or the sender's profile: someone
        # the owner has talked to, but not someone Jarvis can vouch for.
        return Sender(cid, shown or "an unknown number", family=family,
                      vip=bool(shown) and self._is_vip(cid, [shown]), known=bool(shown))

    def _is_vip(self, cid: str, names: list[str]) -> bool:
        wanted = {cid.lower()} | {n.lower() for n in names if n} | {f"name:{n.lower()}" for n in names if n}
        return bool(wanted & self.vip)

    @staticmethod
    def _family_word(name: str, aliases: list[str]) -> bool:
        try:
            from ..notifications import is_family
            return any(is_family(n) for n in [name, *aliases] if n)
        except Exception:  # noqa: BLE001
            return False

    def matches(self, sender: Sender, selectors: Iterable[str]) -> bool:
        """Does this sender fall under any of "group:family", "group:vip", "contact:…", "name:…"?"""
        for sel in selectors:
            sel = str(sel).lower()
            if sel == "group:family" and sender.family:
                return True
            if sel == "group:vip" and sender.vip:
                return True
            if sel == sender.contact_id.lower():
                return True
            if sel.startswith("name:") and sel[5:] in {sender.name.lower(), *(a.lower() for a in sender.aliases)}:
                return True
        return False


def selector_for(name: str, resolver: Resolver) -> str:
    """How the owner's word for someone is stored in a policy: a contact id when known."""
    low = (name or "").strip().lower()
    if low in {"family", "my family", "ghar wale", "gharwale", "family members"}:
        return "group:family"
    if low in {"vip", "vips", "important people", "important contacts"}:
        return "group:vip"
    entry = resolver.find(low)
    return contact_key(str(entry["name"])) if entry else f"name:{low}"
