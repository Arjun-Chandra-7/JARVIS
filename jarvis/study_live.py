"""The Study Companion, connected: Daily Brain routing, the real screen, the teaching overlay.

``jarvis.study`` is a pure package (no provider client, no screen or overlay access — see
tests/test_study_contracts.py). This module is the host side it was designed against:

* ``claims(text, req, session)`` — whether a turn is the companion's. Called by
  ``brain.daily.respond`` after the Brain has classified the turn, so deterministic commands
  (Tier 0) and actions ("message Papa…") never get here, and a question *about* messaging
  ("how would I message Papa?") stays conversation.
* ``ask(text, session)`` — one companion turn; returns the words to say, or None.
* ``LiveContextProvider`` — present-screen and video context from ``jarvis.screen_context`` and
  ``jarvis.screen.youtube`` (the selection, the page in view, an app's accessibility/OCR text, a
  video's caption window). Read only when the question is about the screen or the teacher; a
  password/OTP/banking/private-chat window is not read at all.
* ``OverlayRenderer`` — ``TeachingVisualRequest`` → validated batch → the existing teach bus.
  Nothing is drawn over a lock, password or OTP screen. Cleared on "clear the diagram", "next
  topic" and at the end of a session.
* Revision reminders are *proposed* through the shared approval manager (``jarvis.approvals``);
  the yes adds a local schedule entry. Nothing reaches a calendar, a message or a call.

Starting a study session never turns on the older focus mode (which closes tabs): it is only
offered, and taking it up goes through that mode's own command.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from . import screen_safety
from .study import commands as study_commands
from .study import visuals
from .study.context import ContextSnapshot, TranscriptLine
from .study.types import InputSource, TaskType

_LOCK = threading.Lock()
_STATE: dict = {"companion": None, "renderer": None, "provider": None}

_CLEAR = re.compile(r"(?i)^\s*(?:clear|remove|hide|dismiss)\s+(?:the\s+)?(?:diagram|drawing|board|visual)\b|"
                    r"^\s*next\s+topic\b")
_REMIND = re.compile(r"(?i)\b(?:remind\s+me|set\s+(?:a\s+)?reminder|yaad\s+dila)\b.{0,40}\b(?:revis|study|padh|plan)")
_END = re.compile(r"(?i)\bend\s+(?:the\s+)?(?:study\s+)?session\b|\bstop\s+studying\b")
_START = re.compile(r"(?i)\b(?:start|begin)\b.{0,25}\b(?:study|studying|session|padhai)\b")
_OWN_SOURCE = re.compile(r"(?i)\b(?:according\s+to|from|in|using|based\s+on)\s+(?:my|the|this)\s+"
                         r"(?:pdf|notes?|chapter|book|textbook|document|worksheet|handout)\b")
_MARKERS = re.compile(r"(?i)\b(?:ncert|cbse|class\s*(?:10|x|ten)(?:th)?|\d\s*[- ]?marks?|(?:one|two|three|four|five)"
                      r"[- ]marks?|quiz\s+me|exam\s+answer|board\s+exam|revision\s+plan|chapter\s+\d+)\b|अंक")
_EQUATION = re.compile(r"[A-Za-z0-9)\]]\s*(?:\^\s*\d+|\*\*\s*\d+|[²³])?\s*=\s*[-+√(\dA-Za-z]")
_TEACHER = re.compile(r"(?i)\b(?:teacher|sir|ma'?am|lecturer|video)\b.{0,30}\b(?:say|said|saying|explain|bol)")


# ------------------------------------------------------------------------------ screen / video
def _run(coro):
    """Run a coroutine from sync code, whether or not this thread already has a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("v", asyncio.run(coro)), daemon=True)
    t.start()
    t.join(30)
    return box.get("v")


class LiveContextProvider:
    """``study.context.ContextProvider`` over the running desktop. Read on demand only."""

    def __init__(self, locate=None, youtube=None, read_page=None, safety=screen_safety.check_title) -> None:
        self._locate, self._youtube, self._read_page, self._safety = locate, youtube, read_page, safety
        self.last_note = ""                     # why a snapshot is missing, for an honest reply

    def snapshots(self) -> list[ContextSnapshot]:
        try:
            return _run(self._snapshots()) or []
        except Exception as exc:  # noqa: BLE001 — no context is "please share the screen", not a crash
            self.last_note = f"error:{type(exc).__name__}"
            return []

    async def _snapshots(self) -> list[ContextSnapshot]:
        from . import screen_context as sc
        locate = self._locate or sc.locate
        ctx = await locate(prefer_video=True)
        verdict = self._safety(ctx.app, ctx.window or ctx.title)
        if not verdict.ok:
            self.last_note = f"refused:{verdict.category}"
            return []
        now = time.time()
        title = ctx.title or ctx.window or ctx.app
        if ctx.source == "youtube" and ctx.page is not None:
            from .screen.youtube import YouTube
            yt = (self._youtube or YouTube)(ctx.page)
            state = await yt.state()
            segs, where = await yt.transcript(state)
            self.last_note = "video_transcript" if segs else "video_no_transcript"
            return [ContextSnapshot("video", title=getattr(state, "title", "") or title, captured_at=now,
                                    position_s=getattr(state, "time", None),
                                    transcript=[TranscriptLine(s.start, s.text) for s in segs])]
        if ctx.source == "page" and ctx.page is not None:
            data = await (self._read_page or sc.read_page)(ctx.page)
            snaps = []
            if data.get("selection"):
                snaps.append(ContextSnapshot("selection", title=title, text=str(data["selection"])[:6000],
                                             captured_at=now))
            if data.get("in_view") or data.get("body"):
                snaps.append(ContextSnapshot("browser", title=title, captured_at=now,
                                             text=str(data.get("in_view") or data.get("body"))[:6000]))
            video = data.get("video") or {}
            if video.get("captions"):
                snaps.append(ContextSnapshot("video", title=title, captured_at=now, position_s=video.get("time"),
                                             transcript=[TranscriptLine(float(t), str(x)) for t, x in video["captions"]]))
            self.last_note = "page" if snaps else "page_empty"
            return snaps
        if ctx.source == "app" and ctx.has_text:
            self.last_note = f"app:{ctx.note}"
            conf = None if ctx.note == "atspi" else 0.8
            return [ContextSnapshot("selection" if ctx.selection else "screen", title=title, captured_at=now,
                                    text=(ctx.selection or ctx.in_view or ctx.body)[:6000], ocr_confidence=conf)]
        self.last_note = "nothing_readable"
        return []


# ------------------------------------------------------------------------------ teaching overlay
class OverlayRenderer:
    """``TeachingVisualRequest`` → the teaching overlay, through the existing validated teach bus."""

    def __init__(self, overlay=None, safety=screen_safety.current) -> None:
        self._overlay, self._safety = overlay, safety
        self.shown: list[visuals.TeachingVisualRequest] = []
        self.rejected: list[list[str]] = []
        self.refused: list[str] = []
        self.cleared = 0
        self.active = False

    @property
    def overlay(self):
        if self._overlay is None:
            from .teach.bus import overlay
            self._overlay = overlay()
        return self._overlay

    def _region(self) -> tuple[float, float, float, float]:
        try:
            area = self.overlay.work_area()
            x, y, w, h = (float(area.get(k, d)) for k, d in (("x", 0), ("y", 0), ("w", 1920), ("h", 1080)))
        except Exception:  # noqa: BLE001
            x, y, w, h = 0.0, 0.0, 1920.0, 1080.0
        pw = w * 0.42
        return (x + w - pw - w * 0.03, y + h * 0.12, pw, pw * 0.6)

    def render(self, req: visuals.TeachingVisualRequest) -> bool:
        errs = visuals.validate(req)
        if errs:
            self.rejected.append(errs)
            return False
        verdict = self._safety()
        if not verdict.ok:
            self.refused.append(verdict.category)
            return False
        ov = self.overlay
        gen = ov.new_generation("study")
        batch = visuals.to_overlay_batch(req, lesson="study", region=self._region(), gen=gen)
        sent = ov.send(batch["cmds"], gen=gen)
        if sent:
            self.shown.append(req)
            self.active = True
        return bool(sent)

    def clear(self) -> None:
        self.cleared += 1
        if self.active:
            try:
                self.overlay.control("clear")
            finally:
                self.active = False


# ------------------------------------------------------------------------------ the companion
def companion():
    with _LOCK:
        if _STATE["companion"] is None:
            from .brain.study_gateway import DailyBrainStudyGateway
            from .study.privacy import PrivacyPolicy
            policy = PrivacyPolicy()
            _STATE["renderer"] = _STATE["renderer"] or OverlayRenderer()
            _STATE["provider"] = _STATE["provider"] or LiveContextProvider()
            _STATE["companion"] = study_commands.StudyCompanion(
                DailyBrainStudyGateway(policy), data_dir=study_commands.default_data_dir(), policy=policy,
                context_provider=_STATE["provider"], renderer=_STATE["renderer"], recall=_study_recall)
        return _STATE["companion"]


def use(comp=None, renderer=None, provider=None) -> None:
    """Tests: install a companion (and its renderer/provider) or reset to lazy construction."""
    with _LOCK:
        _STATE.update(companion=comp, renderer=renderer, provider=provider)


def _study_recall(query: str) -> list[str]:
    """Personal memory for *past* study references only, from the study-scoped records."""
    comp = _STATE.get("companion")
    if comp is None:
        return []
    view = comp.mastery.view() if comp.policy.personalization else {"topics": {}}
    words = set(re.findall(r"[a-z]{4,}", (query or "").lower()))
    return [f"{k}: {d.get('estimate', 0):.0%} practice estimate" for k, d in view.get("topics", {}).items()
            if words & set(k.replace("/", " ").replace("_", " ").split())][:5]


_NEW_REQUEST = re.compile(r"(?i)^\s*(?:please\s+)?(?:explain|teach|tell\s+me|what\s+is|what\s+are|how\s+(?:do|does|to)|why|"
                          r"give\s+me|solve|start|make|open|draw|summari[sz]e|define|samjha)\b")
_QUIZ_WORDS = re.compile(r"(?i)\b(?:answer|solution|hint|next|skip)\b")


def _is_new_request(text: str) -> bool:
    t = text or ""
    return bool(_NEW_REQUEST.search(t)) and not _QUIZ_WORDS.search(t)


def routing_active() -> bool:
    """The companion takes turns only while the Daily Brain routes them (JARVIS_DAILY_BRAIN=0 is
    the exact old behaviour) and the companion itself is on (JARVIS_STUDY_COMPANION)."""
    import os
    from .brain import daily
    return daily.enabled() and os.environ.get("JARVIS_STUDY_COMPANION", "1").lower() not in {"0", "false", "no", "off"}


def _session_active() -> bool:
    """A study session is running (it survives restarts: the companion reloads session.json)."""
    try:
        cur = companion().sessions.current
    except Exception:  # noqa: BLE001
        return False
    return bool(cur is not None and not getattr(cur, "paused", False))


def stateful_claim(text: str) -> bool:
    """Turns that belong to the companion because of its own state — no model decides these:
    its session commands, an answer to its waiting quiz question, clearing its diagram, and a
    screen/teacher question while a study session is running."""
    t = text or ""
    if any(rx.search(t) for _, rx in study_commands._SESSION) and study_commands.looks_like_study(t):
        return True                      # "start a science study session" — never an app to open
    comp = _STATE.get("companion")
    if comp is not None and comp.quiz is not None and comp.quiz.current and not t.rstrip().endswith("?") \
            and not _is_new_request(t):
        return True
    if _CLEAR.search(t) and _STATE.get("renderer") is not None and _STATE["renderer"].active:
        return True
    if comp is not None and comp.last_plan is not None and _REMIND.search(t):
        return True                      # a reminder for the plan it just made — proposed, never set directly
    if comp is not None and comp.store.docs and _OWN_SOURCE.search(t):
        return True                      # "according to my PDF…" while the companion holds that PDF
    if _session_active():
        from .study import intent as study_intent
        sreq = study_intent.extract(t)
        if sreq.refers_to_screen or _TEACHER.search(t):
            return True
    return False


def clear_study(text: str) -> bool:
    """A clear study request by its words alone: a named syllabus topic, marks/NCERT/quiz
    markers, or an equation to solve or check. "Why is the sky blue?" is not one."""
    from .study import intent as study_intent
    t = text or ""
    sreq = study_intent.extract(t)
    if sreq.subject and sreq.confidence >= 0.8:
        return True
    if sreq.task in (TaskType.SOLVE, TaskType.EVALUATE) and _EQUATION.search(t):
        return True
    return bool(_MARKERS.search(t))


def defers(text: str) -> bool:
    """For the older deterministic teachers (teach, overlay lesson, video): leave this turn to the
    Daily Brain, which hands it to the companion."""
    try:
        return routing_active() and clear_study(text) and not stateful_claim(text)
    except Exception:  # noqa: BLE001 — never block the older path on a study error
        return False


def claims(text: str, req, session: str = "local") -> bool:
    """Is this turn the companion's? ``req`` is the Brain's classification of it."""
    from .brain.request import Intent, Source
    if req is None or req.source not in Source.TRUSTED:
        return False                     # an incoming message is never a study turn
    if stateful_claim(text):
        return True                      # checked before the action rule: these change nothing outside
    if req.intent in {Intent.ACTION, Intent.MEMORY}:
        return False
    if req.intent == Intent.STUDY:
        return True
    if req.intent not in {Intent.VISION, Intent.CONVERSATION}:
        return False
    if clear_study(text):
        return True
    from .study import intent as study_intent
    sreq = study_intent.extract(text)
    return bool(study_commands.looks_like_study(text) and sreq.refers_to_screen and
                sreq.task in (TaskType.EXPLAIN, TaskType.DEFINE, TaskType.SUMMARIZE, TaskType.ANSWER))


def ask(text: str, session: str = "local", source: InputSource = InputSource.TYPED, brain=None) -> Optional[str]:
    """One companion turn. ``brain`` is the DailyBrain answering this turn: the companion's model
    calls go back through that instance's router, settings and keys."""
    comp = companion()
    renderer = _STATE["renderer"]
    inner = getattr(comp.gateway, "inner", None)
    if brain is not None and hasattr(inner, "brain"):
        inner.brain = brain
    if _CLEAR.search(text or ""):
        renderer.clear()
        if re.match(r"(?i)^\s*next\s+topic", text or ""):
            return "Cleared. What's the next topic?"
        return "Cleared the diagram."
    if _REMIND.search(text or ""):
        return _propose_reminder(comp, session)
    if comp.quiz is not None and comp.quiz.current and _is_new_request(text):
        comp.ask("stop the quiz", source=source)       # a new request is not an answer to the quiz
    resp = comp.ask(text, source=source)
    out = resp.text()
    if _END.search(text or ""):
        renderer.clear()
    if _START.search(text or "") and resp.task == TaskType.SESSION:
        out += ("\n\nFocus mode (which closes distracting tabs and apps) is separate and stays off; "
                "say “turn on study mode” if you want it.")
    provider = _STATE["provider"]
    note = getattr(provider, "last_note", "")
    if "clearer_capture" in resp.needs or "capture" in resp.needs:
        if note == "video_no_transcript":
            out = ("This video has no captions or transcript I can read, so I can't tell you what the teacher "
                   "said. If you turn captions on, or paste the part you mean, I'll explain it.")
        elif note.startswith("refused:"):
            out = screen_safety.Verdict(False, note.split(":", 1)[1]).reason("read it")
    if renderer.refused and resp.visual is not None and not renderer.active:
        out += f"\n\n(I didn't draw the diagram: the screen looks like {renderer.refused[-1]}.)"
    if comp.last_plan is not None and resp.label == "Revision plan":
        out += "\n\nSay “remind me to revise” and I'll ask before adding a reminder."
    return out


def _propose_reminder(comp, session: str) -> str:
    plan = comp.last_plan
    if plan is None or not getattr(plan, "reminder_request", None):
        return "Make a revision plan first — then I can offer a reminder for it."
    from .approvals import MANAGER
    topics = [str(t).split("/")[-1].replace("_", " ") for t in plan.reminder_request.get("topics", [])][:3]
    start = (datetime.now().astimezone() + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    title = "Revise: " + (", ".join(topics) if topics else "study plan")

    def _execute():
        from .agent.omnicore import add_schedule_event
        return {"ok": True, "message": add_schedule_event(title, start.isoformat(),
                                                          (start + timedelta(minutes=30)).isoformat(),
                                                          source="Study Companion")}

    action = MANAGER.propose("reminder", f"add a study reminder “{title}” for tomorrow at 6 pm",
                             {"title": title, "app": "study reminder"}, _execute, session=session)
    return action.prompt()
