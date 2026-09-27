"""Durable people/number memory, stored in the Obsidian vault (git-versioned).

The WhatsApp bridge only knows contacts it has *seen*. When Arjun tells Jarvis "Pradhuman's
number is +91…", that has to be remembered forever — so we persist it here, in the vault, both
as machine-readable JSON (`contacts.json`) and as a human-readable `People/<Name>.md` note that
shows up in Obsidian. Name resolution consults this store FIRST, before the bridge address book.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import CONFIG


def _store() -> Path:
    return CONFIG.vault_path / "contacts.json"


def _load() -> dict:
    try:
        return json.loads(_store().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    p = _store()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_number(raw: str) -> str:
    """Keep leading + and digits only."""
    raw = (raw or "").strip()
    plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    return ("+" if plus else "") + digits


def _key(name: str) -> str:
    return (name or "").strip().lower()


def default_country_code() -> str:
    return re.sub(r"\D", "", os.environ.get("JARVIS_DEFAULT_COUNTRY_CODE", "91")) or "91"


def dialable(raw: str, country_code: str | None = None) -> str:
    """A number WhatsApp can look up: country code and digits, nothing else.

    Phone address books are full of local numbers — ``98xxxxxxxx``, ``098xxxxxxxx`` — and handing
    those to WhatsApp as they are asks it about a number in whatever country the first digits
    happen to name. Local forms get the home country code; international ones are kept.
    """
    raw = (raw or "").strip()
    cc = country_code or default_country_code()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return cc + digits[1:]
    if len(digits) == 10:
        return cc + digits
    return digits


def mask_number(raw: str) -> str:
    """Enough to tell two numbers apart when read aloud or logged, not enough to dial."""
    digits = re.sub(r"\D", "", raw or "")
    return f"ending {digits[-4:]}" if len(digits) >= 4 else "unknown number"


def remember(name: str, number: str = "", note: str = "", aliases: list[str] | tuple[str, ...] = (),
             relationship: str = "") -> dict:
    """Store/update a person. Returns {ok, message}.

    ``aliases`` are what the person is called in speech — "Papa", "Dad", a nickname. An alias is
    the strongest evidence the resolver has, stronger than a name match, because the owner said it.
    """
    name = (name or "").strip()
    if not name:
        return {"ok": False, "message": "Who should I remember? I need a name."}
    number = _norm_number(number)
    data = _load()
    entry = data.get(_key(name), {"name": name})
    entry["name"] = name
    if number:
        entry["number"] = number
    if note:
        entry["note"] = note
    if relationship:
        entry["relationship"] = relationship.strip().lower()
    known = list(entry.get("aliases", []))
    for alias in aliases or ():
        alias = (alias or "").strip()
        if alias and _canon(alias) not in {_canon(a) for a in known}:
            known.append(alias)
    if known:
        entry["aliases"] = known
    data[_key(name)] = entry
    _save(data)
    _write_note(entry)
    bits = [f"Got it — I'll remember {name}"]
    if number:
        bits.append(f"({mask_number(number)})")
    if aliases:
        bits.append("as " + ", ".join(a for a in aliases if a))
    return {"ok": True, "message": " ".join(bits) + "."}


def _write_note(entry: dict) -> None:
    """A friendly Obsidian note so it's visible + searchable in the vault."""
    try:
        folder = CONFIG.vault_path / "People"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\- ]", "", entry["name"]).strip() or "unknown"
        body = [f"# {entry['name']}", ""]
        if entry.get("number"):
            body.append(f"- **Number:** {entry['number']}")
        if entry.get("note"):
            body.append(f"- **Notes:** {entry['note']}")
        body.append("")
        body.append("*(remembered by Jarvis)*")
        (folder / f"{safe}.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def lookup(name: str) -> dict | None:
    """The stored person this name or alias means, or None.

    Exact only. This used to fall back to substring and shared-word matches and return the first
    hit, which is how "man" reached a stored "Pradyumna" — and the caller sent to it.
    """
    q = _canon(name)
    if not q:
        return None
    for entry in _load().values():
        if q == _canon(entry.get("name", "")) or q in {_canon(a) for a in entry.get("aliases", [])}:
            return entry
    return None


def all_contacts() -> list[dict]:
    return list(_load().values())


# --------------------------------------------------------------------------- resolution
#
# One question, asked by everything that addresses a person: *who does the owner mean?* The
# answer is a ranked list with a confidence, and the caller acts only on a single clear winner.
# Three address books feed it — people the owner told Jarvis about (with aliases), the phone's own
# contacts, and the names the WhatsApp bridge has learned — and each can be wrong in its own way,
# so none of them is allowed to pick on its own.

# What a relationship is called, in the ways it gets said. Tried only when nothing is saved under
# the literal word, so a contact actually named "Mummy" wins over one named "Mom".
_RELATIONS: dict[str, tuple[str, ...]] = {
    "father": ("papa", "papa ji", "papaji", "dad", "daddy", "father", "pappa", "pitaji", "pita ji",
               "abba", "baba", "पापा", "पिताजी"),
    "mother": ("mummy", "mumma", "mom", "mommy", "mother", "maa", "ma", "mummyji", "mummy ji",
               "ammi", "मम्मी", "माँ", "मां"),
    "brother": ("bhai", "bhaiya", "brother", "bro", "भाई", "भैया"),
    "sister": ("didi", "sister", "sis", "दीदी"),
    "grandmother": ("dadi", "nani", "grandma", "दादी", "नानी"),
    "grandfather": ("dadu", "dada", "nana", "grandpa", "दादा", "नाना"),
}
_RELATION_OF = {word: rel for rel, words in _RELATIONS.items() for word in words}

# Said around a name but not part of it: "message Papa on WhatsApp", "text my dad", "papa ko".
_NOISE = re.compile(
    r"(?i)^(?:to\s+|my\s+)+|\s+(?:on|via|over|through|in|par|pe)\s+(?:whats\s?app|wa|telegram|"
    r"instagram|insta|sms|text|signal)\b.*$|\s+(?:ko|se|ka|ki|ke)$|'s$")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")

AUTO_SELECT = 0.9       # a single candidate at or above this is sent to without asking
SUGGEST = 0.45          # below this a candidate is not worth offering


def _canon(text: str) -> str:
    text = _EMOJI.sub("", str(text or "")).lower()
    text = re.sub(r"[^\w\s\u0900-\u097F]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_recipient(said: str) -> str:
    """The person's name out of what was said about them."""
    text = (said or "").strip().strip("\"'")
    for _ in range(3):
        stripped = _NOISE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return text


@dataclass
class Candidate:
    name: str
    number: str = ""        # dialable: country code + digits
    jid: str = ""
    score: float = 0.0
    source: str = ""        # "saved", "phone", "whatsapp"
    why: str = ""

    @property
    def address(self) -> str:
        return self.jid or self.number


@dataclass
class Resolution:
    query: str
    status: str             # "resolved", "ambiguous", "not_found"
    best: Candidate | None = None
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "resolved"

    def question(self) -> str:
        if self.status == "not_found":
            return (f"I don't have '{self.query}' in your contacts. Tell me their number with the "
                    "country code, or who they are, and I'll remember them.")
        names = _distinct_labels(self.candidates[:4])
        return f"Which {self.query} — {_or_list(names)}?"


def _distinct_labels(cands: list[Candidate]) -> list[str]:
    counts: dict[str, int] = {}
    for c in cands:
        counts[_canon(c.name)] = counts.get(_canon(c.name), 0) + 1
    return [f"{c.name} ({mask_number(c.number)})" if counts[_canon(c.name)] > 1 and c.number else c.name
            for c in cands]


def _or_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


def _score(query: str, name: str) -> tuple[float, str]:
    """How strongly ``name`` answers to ``query``. Both already canonical."""
    if not query or not name:
        return 0.0, ""
    if query == name:
        return 1.0, "exact name"
    q, n = query.split(), name.split()
    # "papa" → "Papa ji": the whole query, and the rest is only an honorific or emoji residue.
    if n[: len(q)] == q and all(w in {"ji", "jee", "sir", "maam", "sahab"} for w in n[len(q):]):
        return 0.95, "name with honorific"
    if n[: len(q)] == q:
        return 0.6, "name starts with it"
    if set(q) <= set(n):
        return 0.5, "every word appears in the name"
    if len(query) >= 4 and query in name:
        return 0.35, "part of the name"
    return 0.0, ""


def _gather(query: str, whatsapp_candidates) -> list[Candidate]:
    out: list[Candidate] = []
    for entry in _load().values():
        number = dialable(entry.get("number", ""))
        aliases = {_canon(a) for a in entry.get("aliases", [])}
        if query in aliases:
            out.append(Candidate(entry["name"], number, score=1.0, source="saved", why="your alias"))
            continue
        s, why = _score(query, _canon(entry.get("name", "")))
        if s:
            out.append(Candidate(entry["name"], number, score=min(1.0, s + 0.02), source="saved", why=why))
    try:
        from . import phone_contacts
        for c in phone_contacts.all_contacts():
            s, why = _score(query, _canon(c["name"]))
            if s:
                out.append(Candidate(c["name"], dialable(c["number"]), score=s, source="phone", why=why))
    except Exception:  # noqa: BLE001 — an unreadable address book is an empty one
        pass
    for c in (whatsapp_candidates(query) if whatsapp_candidates else []) or []:
        s, why = _score(query, _canon(c.get("name", "")))
        if s:
            jid = c.get("jid", "")
            number = jid.split("@", 1)[0] if jid.endswith("@s.whatsapp.net") else ""
            out.append(Candidate(c["name"], number, jid=jid, score=s - 0.02, source="whatsapp", why=why))
    return out


def _merge(cands: list[Candidate]) -> list[Candidate]:
    """One entry per person: the same number from two address books is one candidate."""
    by_key: dict[str, Candidate] = {}
    for c in sorted(cands, key=lambda c: -c.score):
        key = c.number or c.jid or _canon(c.name)
        kept = by_key.get(key)
        if kept is None:
            by_key[key] = c
        elif not kept.jid and c.jid:
            kept.jid = c.jid
    return sorted(by_key.values(), key=lambda c: -c.score)


def resolve(said: str, whatsapp_candidates=None) -> Resolution:
    """Who ``said`` means. ``whatsapp_candidates(name) -> [{jid, name}]`` adds the bridge's book.

    Resolved only when exactly one person clears ``AUTO_SELECT``. Two different numbers both
    saved as "Papa" is a question, not a coin toss.
    """
    query = _canon(clean_recipient(said))
    if not query:
        return Resolution(said, "not_found")
    cands = _merge(_gather(query, whatsapp_candidates))
    strong = [c for c in cands if c.score >= AUTO_SELECT]
    if not strong and query in _RELATION_OF:
        # Nothing is saved under the literal word. Try the other words for the same person —
        # "dad" finds a contact saved as "Papa" — but only as whole names, never fragments.
        relation = _RELATION_OF[query]
        for other in _RELATIONS[relation]:
            if other != query:
                for c in _merge(_gather(other, whatsapp_candidates)):
                    if c.score >= AUTO_SELECT:
                        c.score, c.why = 0.9, f"{relation}: saved as {c.name}"
                        cands.append(c)
        cands = _merge(cands)
        strong = [c for c in cands if c.score >= AUTO_SELECT]
    if len(strong) > 1 and any(c.source in {"saved", "phone"} for c in strong):
        # A name only the WhatsApp bridge knows is often a profile name the other person chose
        # for themselves — somebody else's father calls himself "Papa" too. A name the owner saved
        # outranks it; the bridge-only one is dropped rather than turned into a question.
        strong = [c for c in strong if c.source in {"saved", "phone"}]
    shown = clean_recipient(said) or said
    if len(strong) == 1:
        return Resolution(shown, "resolved", strong[0], cands)
    if strong:
        return Resolution(shown, "ambiguous", None, strong)
    weak = [c for c in cands if c.score >= SUGGEST]
    return Resolution(shown, "ambiguous" if weak else "not_found", None, weak[:5])
