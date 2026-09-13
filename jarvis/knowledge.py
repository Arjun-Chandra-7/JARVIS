"""Notes that load themselves into the prompt when the subject comes up.

Semantic retrieval finds *tools* for a turn, and the memory store finds *facts* that were said.
Neither covers standing instructions: "whenever I mention the thesis, the deadline is 30 November
and my advisor is Dr Rao — and never suggest I email him before 10am." That is knowledge plus
policy, it applies conditionally, and until now the only place to put it was the system prompt,
where it is paid for on every single turn whether or not it is relevant.

Borrowed from OpenHands' microagents. A knowledge note is a Markdown file with frontmatter:

    ---
    name: thesis
    triggers: [thesis, dissertation, chapter three]
    ---
    The thesis is due 30 November. The advisor is Dr Rao, who prefers questions in writing.
    Never offer to email him before 10am — he reads mail once, mid-morning.

When any trigger appears in the utterance, the body is injected for that turn only. `always: true`
pins a note to every turn for the handful of things that genuinely are always relevant.

The notes live in the vault (`Jarvis/knowledge/`), not the repo, so they are editable in Obsidian
alongside everything else Jarvis knows — and so teaching it something new never requires a commit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

SUBDIR = "Jarvis/knowledge"

#  Injecting more than this per turn defeats the purpose: the point is to spend context only on
#  what is relevant, and a small model drowns in a wall of instructions regardless of relevance.
MAX_NOTES = 4
MAX_CHARS = 1400


@dataclass
class Note:
    name: str
    triggers: list[str] = field(default_factory=list)
    body: str = ""
    always: bool = False
    source: str = ""

    def matches(self, text: str) -> bool:
        if self.always:
            return True
        return any(t and t in text for t in self.triggers)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML frontmatter: `key: value` and `key: [a, b]`. No dependency, no surprises."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    meta: dict = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value.strip("\"'")
    return meta, body.lstrip("\n")


def parse_note(text: str, source: str = "") -> Optional[Note]:
    meta, body = _parse_frontmatter(text)
    body = body.strip()
    if not body:
        return None
    triggers = meta.get("triggers") or []
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split(",") if t.strip()]
    always = str(meta.get("always", "")).strip().lower() in {"1", "true", "yes", "on"}
    name = str(meta.get("name") or Path(source).stem or "note")
    if not triggers and not always:
        # A note with no trigger and no `always` can never fire. Treat the filename as the
        # trigger rather than silently keeping a note that does nothing.
        triggers = [name.lower().replace("-", " ").replace("_", " ")]
    return Note(name=name, triggers=[t.lower() for t in triggers], body=body,
                always=always, source=source)


def directory(vault) -> Path:
    return Path(vault) / SUBDIR


def load(vault) -> list[Note]:
    d = directory(vault)
    if not d.is_dir():
        return []
    notes = []
    for path in sorted(d.glob("*.md")):
        try:
            note = parse_note(path.read_text(encoding="utf-8", errors="ignore"), path.name)
        except OSError:
            continue
        if note is not None:
            notes.append(note)
    return notes


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def select(notes: Iterable[Note], utterance: str, limit: int = MAX_NOTES) -> list[Note]:
    text = _normalise(utterance)
    hits = [n for n in notes if n.matches(text)]
    # `always` notes first — they are the standing rules, and a truncation should drop the
    # conditional extras rather than the policy that holds all the time.
    hits.sort(key=lambda n: (not n.always, n.name))
    return hits[:limit]


def context_for(vault, utterance: str, limit: int = MAX_NOTES) -> str:
    """The block to put in front of the model for this turn, or "" when nothing applies."""
    hits = select(load(vault), utterance, limit)
    if not hits:
        return ""
    parts, used = [], 0
    for note in hits:
        body = note.body.strip()
        if used + len(body) > MAX_CHARS:
            body = body[: max(0, MAX_CHARS - used)].rstrip()
            if not body:
                break
        parts.append(f"## {note.name}\n{body}")
        used += len(body)
    return ("Standing notes that apply to this request. Follow them; do not read them out.\n\n"
            + "\n\n".join(parts))


EXAMPLE = """---
name: example
triggers: [example topic, sample subject]
always: false
---
Replace this file with something true. Anything written here is given to Jarvis whenever one of
the triggers above appears in what you say, and only then.

Good things to put in a knowledge note:
  - facts that do not change often (deadlines, addresses, who is who)
  - standing instructions ("when I ask about invoices, always check the shared drive first")
  - preferences that only apply to one topic ("keep gym plans to three sessions a week")

Set `always: true` for a note that should apply to every single turn — use that sparingly.
"""


def ensure_example(vault) -> Optional[Path]:
    """Create the directory with one self-explaining file, so the feature is discoverable."""
    d = directory(vault)
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / "example.md"
        if not path.exists() and not any(d.glob("*.md")):
            path.write_text(EXAMPLE, encoding="utf-8")
            return path
    except OSError:
        pass
    return None
