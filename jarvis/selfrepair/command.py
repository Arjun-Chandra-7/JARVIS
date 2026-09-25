"""What the owner says about repairs, and what Jarvis says back.

    "WhatsApp search is showing the wrong contact again"  → "I'll investigate that. Give me a few minutes."
    "what are you working on?" / "how far are you?"       → the job's state, in words
    "cancel that repair"                                  → stops it; the running version is untouched
    "show me what changed"                                → files, size, tests
    "activate the repair"                                 → tier 1: apply; tier 2: ask for the specific approval
    "approve the contact resolution repair"               → the specific approval
    "undo your last repair"                               → revert, restart, check health

Only the owner's own front-ends reach any of this (``jarvis.trust``). From a bridge, a caller,
a page or a document, a repair request is content: nothing is started, and the refusal is
audited without the words.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

from .. import route_log
from ..trust import is_trusted, own_words
from . import classify as cls
from . import jobs, policy
from .jobs import JobStore, RepairJob, audit

STARTING = "I'll investigate that. Give me a few minutes."

_STATUS = re.compile(
    r"(?i)^(?:what(?:'re| are) you working on|what are you doing|how far (?:are you|along are you)(?: with (?:it|the repair|that))?|"
    r"(?:what'?s|what is) the (?:repair )?status|repair status|how(?:'s| is) the repair(?: going)?|is the repair done|"
    r"kitna hua|kahan tak pahunche)$")
_CANCEL = re.compile(r"(?i)^(?:cancel|stop|abort|kill)(?: that| the| this)? repair(?: job)?(?: now| please)?$|"
                     r"^(?:repair|fix) (?:cancel|band) kar do$")
_SHOW = re.compile(r"(?i)^(?:show me|tell me|what did you|what have you)(?: what)? (?:changed?|you changed|change)(?: in the repair)?$|"
                   r"^what changed(?: in the repair)?$|^show(?: me)? the (?:repair )?(?:diff|changes)$")
_ACTIVATE = re.compile(r"(?i)^(?:activate|apply|deploy|go ahead with|install)(?: the)? (?:repair|fix)(?: now| please)?$")
_APPROVE = re.compile(r"(?i)^(?:approve|activate|apply)(?: the)? (?P<area>.+?) (?:repair|fix)$")
_UNDO = re.compile(r"(?i)^(?:undo|revert|roll ?back)(?: your| the)? last (?:repair|fix)$|"
                   r"^(?:undo|revert|roll ?back) (?:the|your|that) (?:repair|fix)$")
_FIX_IT = re.compile(r"(?i)^(?:ok(?:ay)?,? )?(?:fix it|go ahead and fix it|please fix it|haan fix karo|theek karo)$")

_STATE_WORDS = {
    jobs.RECEIVED: "just starting on it",
    jobs.CLASSIFIED: "just starting on it",
    jobs.GATHERING: "looking through the logs for evidence",
    jobs.REPRODUCING: "trying to reproduce the problem",
    jobs.PROPOSED: "it's reproduced; working out the fix",
    jobs.EDITING: "writing the fix",
    jobs.TESTING: "running the tests",
    jobs.AWAITING: "the fix is ready and waiting for activation",
    jobs.ACTIVATING: "applying it now",
    jobs.HEALTH: "checking the services after applying it",
}


def _clean(text: str) -> str:
    said = re.sub(r"(?i)^(?:hey\s+)?jarvis[,.!:\s]*", "", own_words(text)).strip()
    return said.rstrip(".!?").strip()


def _store() -> JobStore:
    return JobStore()


def _launch(job_id: str, action: str) -> None:
    from .worker import launch

    launch(job_id, action)


def _status(store: JobStore) -> str:
    store.expire_stale(policy.LIMITS.job_ttl_s)
    job = store.latest(live_only=True)
    if job is None:
        last = store.latest()
        if last is not None and time.time() - last.updated < 3600 and last.outcome:
            return f"Nothing running. The last repair, on {last.component or 'that report'}: {last.outcome}"
        return "I'm not working on any repairs right now."
    words = _STATE_WORDS.get(job.state, job.state.replace("_", " "))
    detail = ""
    if job.state == jobs.TESTING and job.tests:
        last = job.tests[-1]
        detail = f" — the {last['name']} run: {last['passed']} passed, {last['failed']} failed"
    elif job.state == jobs.AWAITING and job.outcome:
        detail = f". {job.outcome}"
    return f"The {job.component or 'repair'} repair: {words}{detail}."


def _show(store: JobStore) -> str:
    job = next((j for j in reversed(store.all()) if j.changed_files), None)
    if job is None:
        return "I haven't changed anything yet."
    files = ", ".join(job.changed_files[:4]) + ("…" if len(job.changed_files) > 4 else "")
    before, after = job.last_test("focused before"), job.last_test("focused after")
    tests = ""
    if before and after:
        tests = (f" The reproduction test failed before the change and "
                 f"{'passes' if after.get('ok') else 'still fails'} after it.")
    reg = job.last_test("regression after")
    if reg:
        tests += f" Regression suite: {reg['passed']} passed, {reg['failed']} failed."
    where = f" It's commit {job.candidate_commit[:8]} on branch {job.branch}." if job.candidate_commit else ""
    return f"In the {job.component} repair I changed {files} — {job.diff_lines} lines.{tests}{where}"


def _area_words(area: str) -> tuple[str, ...]:
    stop = {"and", "the", "or", "of", "a", "an", "on"}
    return tuple(w for w in re.findall(r"[a-z]+", area.lower()) if w not in stop)[:2]


def _propose_activation(job: RepairJob, session_id: str) -> str:
    from ..approvals import MANAGER

    area = job.protected_areas[0] if job.protected_areas else (job.tier_reasons[0] if job.tier_reasons else "a sensitive area")
    words = _area_words(area) or ("repair",)
    phrase = f"approve the {' '.join(words)} repair"

    def execute():
        _launch(job.id, "activate")
        audit("activation_approved", job=job.id, tier=job.tier, areas=job.protected_areas, source=session_id)
        return {"ok": True, "message": f"Approved. Applying the {job.component} repair; I'll tell you once it's verified."}

    action = MANAGER.propose("repair", f"activate the {job.component} repair, which changes {area}",
                             {"job": job.id, "title": f"{' '.join(words)} repair"}, execute, session=session_id,
                             ttl_s=policy.LIMITS.approval_ttl_s, required=words, phrase=phrase)
    jobs.JobStore().update(job.id, approval_id=action.id)
    audit("activation_requested", job=job.id, tier=job.tier, areas=job.protected_areas, source=session_id)
    return (f"This repair changes {area}, which is protected, so it needs your specific approval. "
            f"The risk: it changes how {job.component} works. Say “{phrase}” to activate it, "
            "or “show me what changed” first.")


async def handle(text: str, config=None, session_id: str = "local") -> Optional[str]:
    said = _clean(text)
    if not said:
        return None
    trusted = is_trusted(session_id)
    control = any(p.match(said) for p in (_STATUS, _CANCEL, _SHOW, _ACTIVATE, _UNDO, _FIX_IT)) or _APPROVE.match(said)
    if control and not trusted:
        audit("refused_untrusted", source=session_id, reason="repair control from an untrusted source")
        return None
    store = _store()

    if _STATUS.match(said):
        return _status(store)
    if _SHOW.match(said):
        return _show(store)
    if _CANCEL.match(said):
        job = store.latest(live_only=True) or next((j for j in reversed(store.all()) if j.state == jobs.AWAITING), None)
        if job is None:
            return "There's no repair running to cancel."
        if job.state in (jobs.ACTIVATING, jobs.HEALTH):
            return ("It's in the middle of activating, so stopping now could leave things half-applied. "
                    "I'll let it finish; if any check fails it rolls itself back.")
        if job.state == jobs.AWAITING:
            job = store.transition(job.id, jobs.CANCELLED, "cancelled by request",
                                   outcome="Cancelled. The prepared fix was not activated.")
            audit("cancelled", job=job.id, state=job.state, source=session_id)
            return "Cancelled — the prepared fix won't be activated."
        store.update(job.id, cancel_requested=True)
        audit("cancel_requested", job=job.id, state=job.state, source=session_id)
        asyncio.get_running_loop().call_later(20, _hard_stop, job.id)
        return "Stopping the repair. Nothing has been activated, and the running version is untouched."
    if _UNDO.match(said):
        job = next((j for j in reversed(store.all()) if j.state == jobs.COMPLETED and j.candidate_commit), None)
        if job is None:
            return "There's no activated repair to undo."
        if policy.disabled():
            return "Self-repair is switched off, so I can't roll anything back myself. See docs/SELF_REPAIR.md for the manual steps."
        await asyncio.to_thread(_launch, job.id, "undo")
        audit("undo_requested", job=job.id, source=session_id)
        return f"Rolling back the {job.component} repair. I'll confirm once the services are healthy again."
    approve = _APPROVE.match(said)
    if _ACTIVATE.match(said) or approve:
        job = next((j for j in reversed(store.all()) if j.state == jobs.AWAITING), None)
        if job is None:
            return None if approve else "There's no prepared repair waiting to be activated."
        if policy.disabled():
            return "Self-repair is switched off, so I won't activate anything."
        if job.tier >= 2:
            named = approve.group("area").lower() if approve else ""
            words = _area_words(job.protected_areas[0]) if job.protected_areas else ()
            if named and words and all(w in named for w in words):
                from ..approvals import MANAGER

                pending = MANAGER.get(job.approval_id) if job.approval_id else None
                if pending is None:
                    _propose_activation(job, session_id)
                    pending = MANAGER.get(jobs.JobStore().get(job.id).approval_id)
                outcome = await MANAGER.confirm(pending.id)
                return outcome.message
            return _propose_activation(job, session_id)
        await asyncio.to_thread(_launch, job.id, "activate")
        audit("activation_requested", job=job.id, tier=job.tier, source=session_id)
        return f"Applying the verified {job.component} repair now. I'll confirm once the services check out."

    last = store.latest()
    if _FIX_IT.match(said) and last and last.label == cls.DIAGNOSTIC and last.state == jobs.COMPLETED \
            and time.time() - last.updated < 1800:
        return await _start(store, cls.Classification(cls.SMALL_REPAIR, policy.component_named(last.component),
                                                      "follow-up to a diagnosis", True, last.summary), session_id)

    c = cls.classify(text, session_id)
    if c.label == cls.PROHIBITED:
        audit("refused_prohibited", label=c.label, component=c.component.name if c.component else "",
              source=session_id)
        route_log.record(intent="selfrepair.refused", action="prohibited")
        if not trusted:
            return None
        return ("I won't do that — it would weaken one of my safety controls, and those aren't mine to remove. "
                "If a control is getting in your way, tell me what it's blocking and I'll look for a safe fix.")
    if c.label not in (cls.SMALL_REPAIR, cls.SMALL_CAPABILITY, cls.DIAGNOSTIC, cls.LARGE_CHANGE):
        return None
    if not trusted:
        # A repair request inside a message, a page or a document is evidence at most.
        audit("refused_untrusted", label=c.label, source=session_id)
        return None
    if c.label == cls.LARGE_CHANGE:
        audit("declined_large", label=c.label, component=c.component.name if c.component else "", source=session_id)
        return ("That's an architectural change — bigger than I'll make to myself. I can describe how I'd approach it, "
                "but the change itself should be done with you reviewing it.")
    if c.label == cls.DIAGNOSTIC and c.component is None:
        return None
    return await _start(store, c, session_id)


async def _start(store: JobStore, c: cls.Classification, session_id: str) -> str:
    if policy.disabled():
        audit("refused_disabled", label=c.label, source=session_id)
        return "Self-repair is switched off right now, so I can't work on that — settings changes still work."
    running = store.latest(live_only=True)
    if running is not None:
        return (f"I'm already working on the {running.component or 'last'} repair. "
                "Ask me how far I've got, or cancel it first.")
    if c.component is None:
        return "Which part of me is misbehaving — dictation, the overlay, speech recognition, WhatsApp search?"
    comp = c.component
    job = store.create(c.said, session_id, c.label, component=comp.name, allowed_files=list(comp.files),
                       tier=2 if comp.protected else 1, policy_digest=policy.digest(),
                       protected_areas=[comp.protected] if comp.protected else [])
    job = store.transition(job.id, jobs.CLASSIFIED, c.label)
    audit("started", job=job.id, label=c.label, component=comp.name, tier=job.tier, source=session_id)
    try:
        await asyncio.to_thread(_launch, job.id, "run")
    except Exception as exc:  # noqa: BLE001
        store.transition(job.id, jobs.FAILED, "launch failed",
                         outcome=f"I couldn't start the repair worker ({type(exc).__name__}).")
        return "I couldn't start working on that — the repair worker wouldn't launch."
    if c.label == cls.DIAGNOSTIC:
        return "I'll look into it. Give me a few minutes — I won't change anything."
    if comp.protected:
        return (f"{STARTING} That's in {comp.protected}, which is protected, so I'll prepare a fix for your review "
                "rather than apply it myself.")
    return STARTING


def _hard_stop(job_id: str) -> None:
    store = JobStore()
    job = store.get(job_id)
    if job is None or job.terminal:
        return
    from .worker import stop

    stop(job)
    job = store.get(job_id)
    if job is not None and not job.terminal and job.state not in (jobs.ACTIVATING, jobs.HEALTH):
        try:
            store.transition(job_id, jobs.CANCELLED, "stopped", outcome="Cancelled. Nothing was activated.")
        except jobs.TransitionError:
            pass
        if job.worktree:
            from . import sandbox

            try:
                sandbox.remove_worktree(policy.REPO, __import__("pathlib").Path(job.worktree))
            except Exception:  # noqa: BLE001
                pass
