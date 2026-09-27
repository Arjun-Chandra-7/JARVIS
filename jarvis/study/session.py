"""Study sessions: what we are studying right now, and where we stopped.

A session remembers subject, chapter, goals, mode (explain-then-quiz, hints-only, …), start
time, the active question, the current source, mistakes, completed topics and pending revision.
It survives a restart ("continue from where we stopped") because it is saved to a small JSON file
— without answer text.

A session does **not** keep the microphone open. Whether Jarvis keeps listening between turns is
decided by the existing conversation-session rules; ``StudySession.keep_listening`` is always
False and exists so that the integration can assert it.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .types import new_id


@dataclass
class StudySession:
    session_id: str = field(default_factory=lambda: new_id("SS"))
    subject: str = ""
    chapter: str = ""
    goals: list[str] = field(default_factory=list)
    mode: str = "normal"             # normal | explain_then_quiz | hints_only | exam_practice
    started_at: float = field(default_factory=time.time)
    paused: bool = False
    ended_at: float = 0.0
    active_question: str = ""        # a question id, never the answer
    current_source: str = ""         # a doc id
    last_topic: str = ""             # "chapter/topic", for "iska 5 marks answer do"
    mistakes: list[dict] = field(default_factory=list)   # {"topic", "misconception", "at"} — no answer text
    completed: list[str] = field(default_factory=list)
    pending_revision: list[str] = field(default_factory=list)
    turns: int = 0
    keep_listening: bool = False

    def summary(self) -> dict:
        mins = round(((self.ended_at or time.time()) - self.started_at) / 60)
        return {"subject": self.subject, "chapter": self.chapter, "minutes": mins, "mode": self.mode,
                "completed": list(self.completed), "mistakes": len(self.mistakes),
                "to_revise": list(dict.fromkeys(self.pending_revision))}


class SessionManager:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self.current: Optional[StudySession] = None
        self.last: Optional[StudySession] = None
        if path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("current"):
                self.current = StudySession(**data["current"])
            if data.get("last"):
                self.last = StudySession(**data["last"])

    def start(self, subject: str = "", chapter: str = "", mode: str = "normal", goals: Optional[list[str]] = None) -> StudySession:
        if self.current:
            self.end()
        self.current = StudySession(subject=subject, chapter=chapter, mode=mode, goals=list(goals or []))
        self._save()
        return self.current

    def ensure(self, subject: str = "", chapter: str = "") -> StudySession:
        if not self.current:
            return self.start(subject, chapter)
        if chapter and not self.current.chapter:
            self.current.chapter = chapter
        if subject and not self.current.subject:
            self.current.subject = subject
        return self.current

    def pause(self) -> Optional[StudySession]:
        if self.current:
            self.current.paused = True
            self._save()
        return self.current

    def resume(self) -> Optional[StudySession]:
        s = self.current or self.last
        if s:
            s.paused, s.ended_at = False, 0.0
            self.current = s
            self._save()
        return s

    def end(self) -> Optional[StudySession]:
        s = self.current
        if s:
            s.ended_at = time.time()
            self.last, self.current = s, None
            self._save()
        return s

    def note_mistake(self, topic: str, misconception: str = "") -> None:
        s = self.ensure()
        s.mistakes.append({"topic": topic, "misconception": misconception, "at": round(time.time())})
        if topic:
            s.pending_revision.append(topic)
        self._save()

    def note_topic(self, topic: str, completed: bool = False) -> None:
        if not self.current or not topic:
            return
        self.current.last_topic = topic
        if completed and topic not in self.current.completed:
            self.current.completed.append(topic)
        self.current.turns += 1
        self._save()

    def delete(self) -> None:
        self.current = self.last = None
        if self.path and self.path.exists():
            self.path.unlink()

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"current": asdict(self.current) if self.current else None,
                                   "last": asdict(self.last) if self.last else None}), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)
