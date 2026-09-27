"""What the overlay's Study and 3D views show — summaries and states only.

Study: the running session, the waiting quiz question, the practice estimates (study-scoped
records, labelled as indicators, not assessments), whether a diagram is on screen, and a text box
that sends a study turn through the same path as speech (``study_live.ask`` via the Daily Brain).
3D: the current job's state, mode, honesty label and version — never reference images, OCR text
or file paths. Both are read on demand; neither blocks voice.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


def _study_state() -> dict:
    from . import study_live
    comp = study_live.companion()
    cur = comp.sessions.current
    session = None
    if cur is not None:
        session = {"subject": cur.subject or "", "chapter": cur.chapter or "", "mode": cur.mode or "",
                   "paused": bool(cur.paused), "turns": int(getattr(cur, "turns", 0) or 0)}
    quiz = None
    if comp.quiz is not None and comp.quiz.current is not None:
        q = comp.quiz.current            # the question and its options — never the answer
        quiz = {"prompt": q.prompt[:600], "options": [str(o)[:200] for o in q.options][:6],
                "number": len(comp.quiz.asked), "difficulty": q.difficulty}
    view = comp.mastery.view() if comp.policy.personalization else {"topics": {}, "note": ""}
    progress = [{"topic": k.split("/")[-1].replace("_", " "), "estimate": round(float(d.get("estimate", 0)), 2),
                 "evidence": int(d.get("evidence", 0))} for k, d in list(view.get("topics", {}).items())[:12]]
    renderer = study_live._STATE.get("renderer")
    return {"enabled": study_live.routing_active(), "session": session, "quiz": quiz, "progress": progress,
            "note": view.get("note", ""), "diagram_on_screen": bool(renderer and renderer.active),
            "plan_ready": comp.last_plan is not None}


@router.get("/study/state")
async def study_state():
    return await asyncio.to_thread(_study_state)


class StudyTurn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/study/ask")
async def study_ask(t: StudyTurn):
    from . import study_live
    from .brain import daily
    if not study_live.routing_active():
        return {"reply": "The Study Companion is off (the Daily Brain or JARVIS_STUDY_COMPANION is disabled)."}
    reply = await asyncio.to_thread(study_live.ask, t.text, "local", brain=daily.brain())
    return {"reply": reply or ""}


@router.get("/3d/status")
async def studio_status():
    from .three_d import studio as studio_mod
    s = studio_mod._STUDIO
    if s is None or s.job is None:
        return {"active": False, "summary": "No 3D model in progress."}
    job = s.job
    project = getattr(s, "project", None)
    return {"active": True, "state": job.state.value, "mode": job.mode.value if job.mode else "",
            "fidelity": job.fidelity or "", "stages": list(job.stages_done[-4:]),
            "question": job.question if job.state.value == "needs_input" else "",
            "version": int(getattr(project, "current", 0) or 0), "recent": bool(s.recently_used(600)),
            "summary": s.status()}
