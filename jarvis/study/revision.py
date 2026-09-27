"""Revision plans and spaced repetition — practical, short, and never on the calendar by itself.

A plan fits the time the student actually has ("I have 25 minutes"), puts weak topics and
repeated mistakes first, and always mixes *recall* (say it without looking) with *practice*
(one or two questions) — reading notes alone is the least useful way to spend the minutes.
Blocks are 5–15 minutes; anything over 50 minutes gets a break; a "test tomorrow" plan ends with a
formula run and stops, rather than filling the evening.

Nothing here creates a calendar event or reminder. ``RevisionPlan.reminder_request`` describes
one the student could approve; after integration it goes through the existing approval manager
(``jarvis.approvals``), never straight to a calendar.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Optional

from .curriculum import REGISTRY, Chapter, Registry
from .mastery import MasteryStore, TopicState

INTERVALS_DAYS = (1, 2, 4, 7, 15, 30)


def next_review(state: Optional[TopicState], now: Optional[float] = None) -> float:
    """When to review a topic next: sooner after mistakes, further apart as right answers build up."""
    now = now or time.time()
    if state is None or state.last_reviewed == 0:
        return now
    step = min(state.streak, len(INTERVALS_DAYS) - 1)
    if state.incorrect > state.correct_independent:
        step = 0
    return state.last_reviewed + INTERVALS_DAYS[step] * 86400


@dataclass
class Block:
    minutes: int
    activity: str                    # recall | explain | practice | formula_run | mistakes | break | read
    topic: str
    detail: str


@dataclass
class RevisionPlan:
    minutes: int
    blocks: list[Block]
    focus: list[str]
    notes: list[str] = field(default_factory=list)
    days: list[tuple[str, list[Block]]] = field(default_factory=list)   # multi-day plans
    creates_calendar_events: bool = False
    reminder_request: Optional[dict] = None     # for the approval manager, never executed here

    def total(self) -> int:
        return sum(b.minutes for b in self.blocks)

    def text(self) -> str:
        lines = [f"{b.minutes} min — {b.activity.replace('_', ' ')}: {b.detail}" for b in self.blocks]
        for day, blocks in self.days:
            lines.append(f"{day}: " + "; ".join(f"{b.minutes}m {b.activity} {b.topic.split('/')[-1]}" for b in blocks))
        return "\n".join(lines + self.notes)


def _topic_keys(chapters: list[Chapter]) -> list[str]:
    return [f"{c.id}/{t.id}" for c in chapters for t in c.topics] or [c.id for c in chapters]


def _name(key: str, registry: Registry) -> str:
    cid, _, tid = key.partition("/")
    ch = registry.chapter(cid)
    if ch and tid and ch.topic(tid):
        return ch.topic(tid).name
    return ch.title if ch else key


def plan(minutes: int, *, chapters: list[Chapter], mastery: MasteryStore, exam_in_days: Optional[int] = None,
         weak_only: bool = False, formula_run: bool = False, priorities: Optional[list[str]] = None,
         registry: Registry = REGISTRY, now: Optional[float] = None) -> RevisionPlan:
    minutes = max(5, min(int(minutes), 180))
    keys = _topic_keys(chapters)
    if priorities:
        keys = [k for k in keys if any(p in k for p in priorities)] + [k for k in keys if not any(p in k for p in priorities)]
    ranked = mastery.weakest(keys, n=len(keys))
    mis = mastery.active_misconceptions()
    if weak_only:
        ranked = [k for k in ranked if (mastery.estimate(k) or 0.5) < 0.7] or ranked[:2]
    notes = []
    if not any(k in mastery.topics for k in keys):
        notes.append("No practice history yet, so topics are in chapter order — a quick quiz will let me find your weak spots.")

    if formula_run or minutes <= 8:
        forms = [f for c in chapters for t in c.topics for f in t.formulae]
        blocks = [Block(minutes, "formula_run", chapters[0].id if chapters else "",
                        "Say each formula aloud with its units: " + ", ".join(dict.fromkeys(forms)) if forms else
                        "Run through the key definitions aloud.")]
        return RevisionPlan(minutes, blocks, ranked[:3], notes)

    # Topics to cover: roughly one per 8–10 minutes, weakest first.
    n_topics = max(1, min(len(ranked), minutes // 9))
    focus = ranked[:n_topics]
    blocks: list[Block] = []
    remaining = minutes
    if mis:
        m_min = min(5, remaining // 5)
        if m_min >= 3:
            blocks.append(Block(m_min, "mistakes", mis[0].topic, "Fix your repeated mistake first: " +
                                "; ".join(sorted({m.id.replace('_', ' ') for m in mis[:2]}))))
            remaining -= m_min
    closing = 3 if exam_in_days is not None and exam_in_days <= 1 else 0
    remaining -= closing
    per = max(5, remaining // max(1, len(focus)))
    for i, key in enumerate(focus):
        if remaining <= 0:
            break
        take = min(per, remaining) if i < len(focus) - 1 else remaining
        name = _name(key, registry)
        recall = max(2, take // 3)
        practice = take - recall
        blocks.append(Block(recall, "recall", key, f"{name}: say the key idea and formula without looking, then check."))
        blocks.append(Block(practice, "practice", key, f"{name}: one or two exam-style questions, then mark them."))
        remaining -= take
        if minutes > 50 and i == len(focus) // 2 - 1:
            blocks.append(Block(5, "break", "", "Short break away from the screen."))
    if closing:
        blocks.append(Block(closing, "formula_run", "", "Last formula and definition run-through; then stop and sleep."))
        notes.append("Test tomorrow: no new topics tonight — strengthen what you half-know.")
    plan_ = RevisionPlan(minutes, _fit(blocks, minutes), focus, notes)
    plan_.reminder_request = {"kind": "study_reminder", "needs_approval": True, "topics": focus}
    return plan_


def _fit(blocks: list[Block], minutes: int) -> list[Block]:
    """Trim so the plan never exceeds the time available."""
    out, used = [], 0
    for b in blocks:
        if used >= minutes:
            break
        take = min(b.minutes, minutes - used)
        if take > 0:
            out.append(Block(take, b.activity, b.topic, b.detail))
            used += take
    return out


def week_plan(daily_minutes: int, chapters: list[Chapter], mastery: MasteryStore, *, exam_date: Optional[dt.date] = None,
              today: Optional[dt.date] = None, registry: Registry = REGISTRY) -> RevisionPlan:
    today = today or dt.date.today()
    days = 7 if exam_date is None else max(1, min(7, (exam_date - today).days))
    keys = mastery.weakest(_topic_keys(chapters), n=len(_topic_keys(chapters)))
    out = RevisionPlan(daily_minutes * days, [], keys[:3])
    for d in range(days):
        day = today + dt.timedelta(days=d)
        todays = keys[d % len(keys):] + keys[: d % len(keys)] if keys else []
        p = plan(daily_minutes, chapters=chapters, mastery=mastery, registry=registry,
                 exam_in_days=(exam_date - day).days if exam_date else None, priorities=[k.split("/")[-1] for k in todays[:2]])
        out.days.append((day.strftime("%a %d %b"), p.blocks))
    out.notes.append(f"{daily_minutes} minutes a day; the last day before the exam is recall and formulae only.")
    return out
