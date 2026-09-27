"""Private-safe operational metrics: counts and timings, never content.

Every record goes through ``privacy.safe_event``, which keeps only allow-listed, non-textual
fields. There is no field in which a question, an answer, a document excerpt or a handwriting
image could travel. The host decides whether metrics are written anywhere at all.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .privacy import safe_event


@dataclass
class Metrics:
    events: list[dict] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)

    def request(self, *, request_id: str, task: str, mode: str, language: str, latency_ms: float,
                brain_calls: int, grounded: bool, source_missing: bool, chunks: int = 0) -> dict:
        e = safe_event(event="study_request", request_id=request_id, task=task, mode=mode, language=language,
                       latency_ms=round(latency_ms, 1), brain_calls=brain_calls, grounded=grounded,
                       source_missing=source_missing, chunks=chunks)
        self.events.append(e)
        self.counts[f"task:{task}"] += 1
        if source_missing:
            self.counts["source_missing"] += 1
        return e

    def quiz(self, *, correct: bool, difficulty: int, hints: int) -> dict:
        e = safe_event(event="quiz_answer", correct=correct, difficulty=difficulty, hints=hints)
        self.events.append(e)
        self.counts["quiz_answers"] += 1
        return e

    def snapshot(self) -> dict:
        return dict(self.counts)
