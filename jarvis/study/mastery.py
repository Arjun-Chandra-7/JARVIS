"""Topic-level learning state and misconceptions — the student's, visible and deletable.

What is tracked, per topic (``chapter/topic`` key): seen, explained, practised, correct on their
own, correct with a hint, incorrect, repeated mistakes, last reviewed, self-reported confidence,
and how much evidence there is. Misconceptions are stored **separately** from scores: "confuses
conventional current with electron flow" is a specific thing to fix, not a low number.

What is not tracked, ever: anything about the person rather than the topic. No ability,
intelligence, personality or other trait is inferred from mistakes, and the estimate is labelled
as a practice indicator, not an assessment.

The student can view it, correct it, reset a topic, delete everything, or switch personalisation
off (nothing is recorded and nothing recorded is used). Answer text is not stored unless the
privacy policy says so. Storage is one JSON file with 0600 permissions, written atomically.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .curriculum import MISCONCEPTIONS

DISCLAIMER = "A practice indicator from your sessions here — not a formal assessment."


@dataclass
class TopicState:
    seen: int = 0
    explained: int = 0
    practised: int = 0
    correct_independent: int = 0
    correct_with_hint: int = 0
    incorrect: int = 0
    repeated_mistakes: int = 0
    last_reviewed: float = 0.0
    confidence: float = 0.0          # the student's own rating, 0..1, averaged
    confidence_n: int = 0
    evidence: int = 0
    streak: int = 0                  # consecutive independent-correct answers, for spacing
    override: Optional[float] = None  # the student's correction of the estimate

    @property
    def estimate(self) -> float:
        """0..1. Hinted answers count half; a prior of one right and one wrong keeps early
        estimates modest. The student's own correction wins."""
        if self.override is not None:
            return self.override
        right = self.correct_independent + 0.5 * self.correct_with_hint
        attempts = self.correct_independent + self.correct_with_hint + self.incorrect
        return round((right + 1) / (attempts + 2), 3)


@dataclass
class MisconceptionRecord:
    id: str
    topic: str
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    resolved: bool = False


class MasteryStore:
    def __init__(self, path: Optional[Path] = None, *, personalization: bool = True) -> None:
        self.path = path
        self.topics: dict[str, TopicState] = {}
        self.misconceptions: dict[str, MisconceptionRecord] = {}
        self.personalization = personalization
        if path and path.exists():
            self._load()

    # ---------------------------------------------------------------- recording
    def _state(self, key: str) -> TopicState:
        return self.topics.setdefault(key, TopicState())

    def record(self, key: str, event: str, *, confidence: Optional[float] = None, now: Optional[float] = None) -> None:
        """event: seen | explained | practised | correct | correct_hint | incorrect"""
        if not self.personalization or not key:
            return
        s = self._state(key)
        now = now or time.time()
        if event == "seen":
            s.seen += 1
        elif event == "explained":
            s.explained += 1
            s.seen += 1
        elif event in ("correct", "correct_hint", "incorrect"):
            s.practised += 1
            s.evidence += 1
            if event == "correct":
                s.correct_independent += 1
                s.streak += 1
            elif event == "correct_hint":
                s.correct_with_hint += 1
                s.streak = 0
            else:
                s.incorrect += 1
                s.streak = 0
        s.last_reviewed = now
        if confidence is not None:
            s.confidence = round((s.confidence * s.confidence_n + max(0.0, min(1.0, confidence))) / (s.confidence_n + 1), 3)
            s.confidence_n += 1
        self._save()

    def misconception(self, mid: str, topic_key: str, now: Optional[float] = None) -> Optional[MisconceptionRecord]:
        if not self.personalization or mid not in MISCONCEPTIONS:
            return None
        now = now or time.time()
        rec = self.misconceptions.setdefault(mid, MisconceptionRecord(mid, topic_key, first_seen=now))
        rec.count += 1
        rec.last_seen = now
        rec.resolved = False
        if rec.count >= 2 and topic_key:
            self._state(topic_key).repeated_mistakes += 1
        self._save()
        return rec

    def resolve(self, mid: str) -> None:
        if mid in self.misconceptions:
            self.misconceptions[mid].resolved = True
            self._save()

    # ---------------------------------------------------------------- reading
    def estimate(self, key: str) -> Optional[float]:
        if not self.personalization or key not in self.topics:
            return None
        return self.topics[key].estimate

    def weakest(self, keys: list[str], n: int = 3) -> list[str]:
        if not self.personalization:
            return keys[:n]
        return sorted(keys, key=lambda k: (self.topics[k].estimate if k in self.topics else 0.5,
                                           -self.topics.get(k, TopicState()).repeated_mistakes))[:n]

    def active_misconceptions(self, topic_prefix: str = "") -> list[MisconceptionRecord]:
        if not self.personalization:
            return []
        return [m for m in self.misconceptions.values() if not m.resolved and m.topic.startswith(topic_prefix)]

    def view(self) -> dict:
        """Everything stored, in plain terms, for "show my progress"."""
        return {
            "note": DISCLAIMER,
            "personalization": self.personalization,
            "topics": {k: {**asdict(v), "estimate": v.estimate} for k, v in sorted(self.topics.items())},
            "misconceptions": [{"id": m.id, "what": MISCONCEPTIONS[m.id].label, "fix": MISCONCEPTIONS[m.id].correction,
                                "topic": m.topic, "count": m.count, "resolved": m.resolved}
                               for m in self.misconceptions.values()],
        }

    # ---------------------------------------------------------------- the student's controls
    def correct(self, key: str, estimate: float) -> None:
        self._state(key).override = max(0.0, min(1.0, estimate))
        self._save()

    def reset_topic(self, key: str) -> bool:
        existed = self.topics.pop(key, None) is not None
        for mid in [m for m, r in self.misconceptions.items() if r.topic == key]:
            del self.misconceptions[mid]
        self._save()
        return existed

    def delete_all(self) -> None:
        self.topics.clear()
        self.misconceptions.clear()
        if self.path and self.path.exists():
            self.path.unlink()

    def set_personalization(self, on: bool) -> None:
        self.personalization = on
        self._save(force=True)

    # ---------------------------------------------------------------- storage
    def _save(self, force: bool = False) -> None:
        if not self.path or (not self.personalization and not force):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"personalization": self.personalization,
                "topics": {k: asdict(v) for k, v in self.topics.items()},
                "misconceptions": {k: asdict(v) for k, v in self.misconceptions.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.personalization = data.get("personalization", True)
        self.topics = {k: TopicState(**v) for k, v in data.get("topics", {}).items()}
        self.misconceptions = {k: MisconceptionRecord(**v) for k, v in data.get("misconceptions", {}).items()}
