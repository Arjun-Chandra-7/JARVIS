"""Turn a day of conversation into durable facts — the step that makes memory compound.

Episodes accumulate forever and stay episodes: a hundred turns about a flight are a hundred rows
that all have to be retrieved and re-read for the assistant to know the seat number. Recall gets
slower and vaguer as the store grows, which is the opposite of remembering.

Consolidation is the sleep-time pass that fixes this. Once a day it reads the episodes since the
last run, asks the model which of them contain something durably true about the user — a
preference, a commitment, a name, a decision, an ongoing thread — and writes those as `fact` rows.
Facts are short, dense and directly retrievable, so "what seat am I in" hits one row instead of
reconstructing an answer from a dozen fragments.

It also supersedes. A new fact that closely matches an existing one replaces it rather than sitting
alongside it, because "Arjun's flight is on the 24th" and "Arjun moved his flight to the 26th"
cannot both be true and a retriever has no way to prefer the newer one. Similarity is measured with
the same embedder the store uses; without one, exact-subject matching still catches the common case.

Everything is best effort. No model, no episodes, or a malformed reply and the run is a no-op that
leaves the store exactly as it was.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

LLM = Callable[[str], str]

# Facts closer than this to an existing one are treated as an update to it, not a new belief.
SUPERSEDE_SIMILARITY = 0.86

# Below this many turns there is nothing worth a model call.
MIN_EPISODES = 6

_PROMPT = """You are maintaining the long-term memory of a personal assistant.

Below are things the user and the assistant said recently. Extract only what is DURABLY TRUE and
worth remembering weeks from now:

  - stable preferences and habits ("takes coffee black", "prefers evening meetings")
  - commitments and plans with their details (dates, times, seat numbers, places)
  - facts about people, projects and possessions
  - decisions that were made, and threads explicitly left open

Ignore: greetings, chit-chat, one-off questions, anything the assistant merely reported (system
stats, weather), and anything already obviously transient.

Reply with a JSON array of objects, each {{"fact": "...", "subject": "..."}} where `fact` is one
short self-contained sentence written in the third person about the user, and `subject` is one or
two lowercase words naming what it is about (a person, a project, a topic). Output the JSON array
and nothing else.

Example input:
  User: remember my flight to Delhi is on the 24th at 6am, seat 14C
  Jarvis: Noted.
  User: what's the weather?
  Jarvis: 26 degrees and overcast.
  User: my sister Meera is visiting next month, she's vegetarian
Example output:
[{{"fact": "The user's flight to Delhi is on the 24th at 6am, seat 14C.", "subject": "travel"}},
 {{"fact": "The user's sister Meera is vegetarian and is visiting next month.", "subject": "meera"}}]

Note that the weather exchange produced nothing: it was transient. Only use [] when there is
genuinely nothing of that kind in the input.

Recent exchanges:
{episodes}
"""


def _state_path() -> Path:
    root = Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()
    return root / "consolidated-at"


def last_run() -> float:
    try:
        return float(_state_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0


def _mark_run(when: float) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.write_text(f"{when:.0f}", encoding="utf-8")
    except OSError:
        pass


def _parse_facts(raw: str) -> list[dict]:
    """Pull the JSON array out of a model reply that may be wrapped in prose or a code fence."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact", "")).strip()
        if len(fact) < 8:
            continue
        subject = str(item.get("subject", "")).strip().lower()[:40] or "general"
        out.append({"fact": fact[:400], "subject": subject})
    return out


def _cosine(a, b) -> float:
    import math

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def consolidate(config, llm: Optional[LLM] = None, *, window_hours: float = 26.0,
                store=None, embed=None, now: Optional[float] = None) -> dict:
    """Fold recent episodes into durable facts. Returns a summary of what changed."""
    from .search import _embedder
    from .store import get_store

    now = now if now is not None else time.time()
    store = store or get_store(config.vault_path)
    result = {"episodes": 0, "added": 0, "superseded": 0, "skipped": ""}

    since = now - window_hours * 3600
    episodes = [e for e in store.recent_episodes(limit=400) if e["ts"] >= since]
    result["episodes"] = len(episodes)
    if len(episodes) < MIN_EPISODES:
        result["skipped"] = "not enough recent conversation"
        return result

    if llm is None:
        llm = _default_llm(config)
    if llm is None:
        result["skipped"] = "no model available"
        return result

    lines = [f"{'User' if e['who'] == 'you' else 'Jarvis'}: {e['text'][:300]}" for e in episodes]
    try:
        raw = llm(_PROMPT.format(episodes="\n".join(lines)[:12000]))
    except Exception as exc:  # noqa: BLE001 - a nightly job must never take the process down
        result["skipped"] = f"model error: {exc}"
        return result

    facts = _parse_facts(raw)
    if not facts:
        result["skipped"] = "nothing durable found"
        _mark_run(now)
        return result

    if embed is None:
        embed = _embedder()

    existing = _existing_facts(store, embed)
    for item in facts:
        replaced = _supersede(store, existing, item["fact"], embed)
        store.add_fact(item["fact"], ref=item["subject"], embed=embed)
        result["superseded" if replaced else "added"] += 1

    _mark_run(now)
    return result


def _existing_facts(store, embed) -> list[dict]:
    rows = store.db.execute("SELECT id, ref, text FROM chunks WHERE kind='fact'").fetchall()
    out = []
    for r in rows:
        vec = None
        if embed is not None:
            row = store.db.execute("SELECT vec, dim FROM vecs WHERE chunk_id=?", (r["id"],)).fetchone()
            if row is not None:
                import numpy as np

                vec = np.frombuffer(row["vec"], dtype="float32").tolist()
        out.append({"id": int(r["id"]), "ref": r["ref"], "text": r["text"], "vec": vec})
    return out


def _supersede(store, existing: list[dict], fact: str, embed) -> bool:
    """Delete any existing fact this one replaces. Returns True if something was removed."""
    new_vec = embed(fact) if embed is not None else None
    removed = False
    for old in list(existing):
        same = False
        if new_vec is not None and old["vec"] is not None:
            same = _cosine(new_vec, old["vec"]) >= SUPERSEDE_SIMILARITY
        else:
            # No embedder: fall back to a strong lexical overlap on the content words, which still
            # catches the common "same sentence, one detail changed" case.
            same = _token_overlap(fact, old["text"]) >= 0.7
        if same:
            store.db.execute("DELETE FROM chunks WHERE id=?", (old["id"],))
            existing.remove(old)
            removed = True
    if removed:
        store.db.commit()
    return removed


def _token_overlap(a: str, b: str) -> float:
    ta = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if len(w) > 3}
    tb = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if len(w) > 3}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def _default_llm(config) -> Optional[LLM]:
    """A plain completion against whichever OpenAI-compatible endpoint the brain uses."""
    try:
        base_url, key, model = config.llm_params()
    except Exception:  # noqa: BLE001
        return None
    if not base_url:
        return None

    def call(prompt: str) -> str:
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=key or "none", max_retries=0, timeout=90)
        resp = client.chat.completions.create(
            model=model, temperature=0.1, max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""

    return call


def due(min_interval_hours: float = 20.0, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    return (now - last_run()) >= min_interval_hours * 3600
