"""A lesson, as data: what is said, what is drawn while it is said, and what happens after.

Educational content and rendering are kept apart. A lesson is a list of steps; a step is a list
of phrases; a phrase is a sentence to speak and the overlay commands that belong to it. The
runner sends a phrase's commands at the moment that phrase becomes audible — not before, from an
estimate of how long the previous ones would take.

Nothing in a plan is code. The commands are built by the lesson templates in this package and
validated before they are sent; a language model, where one is used, only ever picks words.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Phrase:
    text: str
    visuals: list = field(default_factory=list)


@dataclass
class Step:
    id: str
    title: str
    phrases: list[Phrase]


@dataclass
class SourceContext:
    kind: str = "standalone"            # "standalone" | "video"
    title: str = ""
    at_s: Optional[float] = None
    caption_language: str = ""
    transcript_used: bool = False       # the explanation quotes or follows the transcript
    triangle_from_screen: bool = False  # the drawing traces something actually on screen


@dataclass
class LessonPlan:
    topic: str
    language: str
    learning_goal: str
    setup: list                         # commands that create the scene, sent before step one
    steps: list[Step]
    lesson_id: str = field(default_factory=lambda: "L" + uuid.uuid4().hex[:10])
    learner_level: str = "Class 10"
    source_context: SourceContext = field(default_factory=SourceContext)
    source_confidence: float = 1.0
    follow_up_options: list[str] = field(default_factory=list)
    checks_for_understanding: list[str] = field(default_factory=list)
    cleanup_policy: dict = field(default_factory=lambda: {"auto_clear_s": 14.0, "keep": False})
    extras: dict = field(default_factory=dict)        # template state follow-ups need (node ids, geometry)

    @property
    def spoken_segments(self) -> list[str]:
        return [p.text for s in self.steps for p in s.phrases]

    @property
    def visual_actions(self) -> list[list]:
        return [p.visuals for s in self.steps for p in s.phrases]

    def timing(self, words_per_s: float = 2.6) -> list[dict]:
        """An estimate for display and for the typed path. The spoken path does not use it: there
        each phrase's pictures wait for that phrase's audio."""
        t, out = 0.0, []
        for s in self.steps:
            for p in s.phrases:
                d = max(0.8, len(p.text.split()) / words_per_s)
                out.append({"step": s.id, "start_s": round(t, 2), "duration_s": round(d, 2)})
                t += d
        return out

    def text(self) -> str:
        return " ".join(self.spoken_segments)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["spoken_segments"] = self.spoken_segments
        d["timing"] = self.timing()
        return d
