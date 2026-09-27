"""The daily-assistant route: questions, study, research and pictures answered without the tool loop.

Where it sits (docs/DAILY_BRAIN.md → Request path):

    utterance → jarvis.commands.handle   (Tier 0: deterministic — no model, ever)
              → GroqAgent.send → gate checks
              → daily.respond()          ← here
                   conversation / writing / planning / code / study / research / vision:
                       answered here: compact context, the router's model, honest notice
                   action / memory ("message Papa …", "remember this"):
                       returns None → the agent's tool loop, on a verified tool model,
                       through the contact and approval pipeline as before

Answering a question here instead of in the tool loop is most of the token saving: the loop
sends the whole standing prompt, ~10 tool schemas and the history on every call; this sends a
short route-specific instruction and only the context that is relevant.

It never acts. A reply here that claims to have sent, opened or played something is replaced —
conversation cannot reach a side effect, and "how would I message Papa?" is answered in words.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import contextengine as ce
from . import executor, research, router, study, telemetry
from .registry import BrainSettings, BrainState, Registry
from .request import BrainRequest, Intent, Source, Tier
from .understand import understand

PERSONA = (
    "You are JARVIS, the owner's assistant on their Linux computer. Talk like a sharp, friendly person: "
    "natural, concise by default, more detail only when asked. Match the user's language exactly — English, "
    "Hindi or Hinglish (Hindi in Latin letters) — and keep their slang where it fits. Don't open with "
    "'Certainly', 'Sure' or 'Great question', don't repeat the question back, and don't mention models or "
    "routing. You have no feelings or personal experiences and never claim any. You cannot take actions in "
    "this reply: if asked how to do something, explain how; never say you did something. If a fact may have "
    "changed recently, say you'd need to check.")
_OPENERS = re.compile(r"^(?:certainly|sure(?: thing)?|of course|absolutely|great question|good question)[!,.]?\s+", re.I)
_CONF = re.compile(r"\n?\s*CONFIDENCE:\s*(high|medium|low)\s*$", re.I)
_CORRECTION = re.compile(r"(?i)^\s*(?:no[,!. ]|nahi|not that|actually[, ]|i meant|galat|wrong[,. ])")
_CONSTRAINT = re.compile(r"(?i)\b(?:from now on|always|never|don'?t ever|hamesha|kabhi mat)\b")
_REASONING = re.compile(r"(?i)\b(?:how many|prove|probability|puzzle|riddle|if .* then|at least|exactly|which of)\b")


@dataclass
class DailyReply:
    text: str
    route: str
    decision: object = None
    notice: str = ""
    model_called: bool = True


def enabled() -> bool:
    return os.environ.get("JARVIS_DAILY_BRAIN", "1").lower() not in {"0", "false", "no", "off"}


class DailyBrain:
    def __init__(self, config=None, registry: Registry | None = None, keystore=None,
                 searcher: Optional[Callable] = None, recall: Optional[Callable] = None):
        if config is None:
            from ..config import CONFIG as config
        self.config = config
        self._registry, self._keystore = registry, keystore
        self._fixed = registry is not None
        self._mtime = 0.0
        self.searcher = searcher
        self.recall = recall
        self.quiz: dict[str, dict] = {}              # session -> {"topic", "question", "lang"}
        self.summaries: dict[str, ce.Summary] = {}
        self.last_context: dict = {}
        self._lock = threading.Lock()
        # The Study Companion answers study turns; JARVIS_STUDY_COMPANION=0 leaves them to the
        # Brain's own study route (brain/study.py).
        self.study_enabled = os.environ.get("JARVIS_STUDY_COMPANION", "1").lower() not in {"0", "false", "no", "off"}

    # -- registry, reloaded when the Brain tab saves settings ---------------------------------
    @property
    def registry(self) -> Registry:
        with self._lock:
            path = BrainSettings().path
            mtime = path.stat().st_mtime if path.exists() else 0.0
            if self._registry is None or (not self._fixed and mtime != self._mtime):
                self._registry = Registry(self.config, BrainSettings(), BrainState())
                self._keystore = None
                self._mtime = mtime
            return self._registry

    @property
    def keystore(self):
        if self._keystore is None:
            from .keys import KeyStore
            reg = self.registry
            self._keystore = KeyStore(reg.settings, reg.state)
        return self._keystore

    def reload(self) -> None:
        with self._lock:
            if not self._fixed:
                self._registry, self._keystore = None, None

    # -- the route ------------------------------------------------------------------------
    def classify(self, text: str, session: str = "local", source: str | None = None,
                 images: list | None = None) -> BrainRequest:
        from ..trust import own_words
        words = own_words(text) or (text or "").strip()
        src = source or Source.from_session(session)
        return understand(BrainRequest(words, source=src, authenticated=src in Source.TRUSTED,
                                       session_id=session, images=list(images or [])))

    def respond(self, text: str, session: str = "local", *, source: str | None = None,
                images: list | None = None, history: list | None = None,
                on_delta=None, pending: list | None = None) -> Optional[DailyReply]:
        if not enabled():
            return None
        req = self.classify(text, session, source, images)
        attached = _attached_context(text)

        # Study turns go to the Study Companion, whose model calls come back through this Brain's
        # capability router (brain/study_gateway.py). Actions and memory never get here.
        if not images and self.study_enabled:
            try:
                from .. import study_live
                if study_live.claims(req.text, req, session):
                    answer = study_live.ask(req.text, session, brain=self)
                    if answer:
                        telemetry.record(request_id=req.request_id, source=req.source, intent=Intent.STUDY,
                                         route="study_companion", language=req.language, privacy=req.privacy,
                                         status="ok")
                        return DailyReply(answer, "study_companion", None, "", True)
            except Exception as exc:  # noqa: BLE001 — the Brain's own study route still answers
                telemetry.record(request_id=req.request_id, route="study_companion",
                                 status=f"error:{type(exc).__name__}")

        quiz = self.quiz.get(session)
        if quiz and req.intent in {Intent.CONVERSATION, Intent.STUDY} and not (req.study and req.study.get("mode") == "quiz"):
            if req.intent == Intent.CONVERSATION and len(req.text.split()) <= 60 and not req.text.rstrip().endswith("?"):
                req.intent = Intent.STUDY
                req.study = study.StudyRequest(subject=quiz.get("subject", ""), chapter=quiz.get("chapter", ""),
                                               mode="check").to_dict()
                attached.insert(0, ("turn", f"Quiz question asked: {quiz['question']}"))
        if req.intent in {Intent.ACTION, Intent.MEMORY}:
            telemetry.record(request_id=req.request_id, source=req.source, intent=req.intent, route="tools",
                             language=req.language, privacy=req.privacy, status="handed_to_agent")
            return None
        if req.intent == Intent.VISION and not req.images:
            return None                               # the agent's screen tools take this

        registry = self.registry
        if req.intent == Intent.STUDY and req.study and req.study.get("mode") == "quote" and not attached:
            return self._no_model(req, "study", study.quote_unavailable(req.language), "quote_unavailable")

        sources = []
        if req.intent == Intent.RESEARCH:
            sources = research.gather(req.text, session, self.searcher)
            if not sources:
                return self._no_model(req, "research", research.unavailable(req.language), "no_sources")

        decision = router.plan(req, registry)
        if not decision.candidates:
            if decision.refused == "no_verified_tool_model":
                return None
            text_out = executor.failure_notice(req, decision, [], registry, 0)
            return self._no_model(req, decision.route, text_out, "no_candidate", decision)

        system, accept, label = self._instruction(req, sources, bool(attached))
        first = registry.model(f"{decision.candidates[0].provider_id}/{decision.candidates[0].model_id}")
        budget = min(req.context_budget, int((first.context_window if first else 8192) * 0.5))
        segments = self._segments(req, system, history or [], pending or [], attached, sources)
        summary = self.summaries.setdefault(session, ce.Summary())
        if req.images:
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": [{"type": "text", "text": req.text}] + [
                            {"type": "image_url", "image_url": {"url": img}} for img in req.images]}]
            pack_report = {"total_before": 0, "total_after": 0}
        else:
            pack = ce.build(req.text + ("\nEnd with a last line 'CONFIDENCE: high' or 'CONFIDENCE: low'."
                                        if accept is _confidence_check else ""),
                            segments, budget, summary=summary)
            messages, pack_report = pack.messages, pack.report
            self.last_context = {"route": decision.route, "at": time.time(), **ce.debug_view(pack)}
        decision.context_tokens_before = pack_report.get("total_before", 0)
        result = executor.execute(req, decision, messages, registry, self._keystore_or_none(),
                                  accept=accept, on_delta=on_delta if not accept else None,
                                  temperature=0.2 if req.intent in {Intent.STUDY, Intent.RESEARCH} else 0.4)
        status = "ok" if result.ok and not decision.quality_reduced else "degraded" if result.ok else "failed"
        telemetry.from_decision(req, decision, status, escalated=result.escalated,
                                context_after=pack_report.get("total_after"), offline=executor.offline())
        if not result.ok:
            return DailyReply(result.notice, decision.route, decision, result.notice, True)

        answer = self._finish(req, result.text, label, sources)
        if req.intent == Intent.STUDY and req.study and req.study.get("mode") == "quiz":
            self.quiz[session] = {"question": answer[-400:], "subject": req.study.get("subject", ""),
                                  "chapter": req.study.get("chapter", "")}
        elif req.intent == Intent.STUDY and req.study and req.study.get("mode") == "check":
            self.quiz.pop(session, None)
        text_out = f"{result.notice} {answer}".strip() if result.notice else answer
        return DailyReply(text_out, decision.route, decision, result.notice, True)

    # -- pieces ---------------------------------------------------------------------------
    def _keystore_or_none(self):
        try:
            return self.keystore
        except Exception:  # noqa: BLE001 - no keyring: env keys still work through a memory store
            from .keys import KeyStore, MemoryBackend
            reg = self.registry
            return KeyStore(reg.settings, reg.state, MemoryBackend())

    def _no_model(self, req, route, text, kind, decision=None) -> DailyReply:
        telemetry.record(request_id=req.request_id, source=req.source, intent=req.intent, route=route,
                         language=req.language, privacy=req.privacy, status="answered_without_model",
                         refused_kind=kind)
        return DailyReply(text, route, decision, "", False)

    def _instruction(self, req: BrainRequest, sources, chapter_supplied: bool):
        if req.intent == Intent.STUDY and req.study:
            s = study.StudyRequest(**{k: v for k, v in req.study.items() if k in study.StudyRequest.__dataclass_fields__})
            accept = (lambda r: None if study.fits_marks(r.text, s.marks) else "answer length does not fit the marks") \
                if s.mode == "formal" and s.marks and s.style != "final_only" else None
            label = study.label(s, chapter_supplied) if s.mode in {"formal", "explain", "revise"} else ""
            return study.system_prompt(s, req.language), accept, label
        if req.intent == Intent.RESEARCH:
            return research.SYSTEM, None, ""
        accept = _confidence_check if (_REASONING.search(req.text) and 0.15 <= req.difficulty < 0.6) else None
        style = {"short": " Answer in one or two sentences.", "final_only": " Give only the answer.",
                 "steps": " Give numbered steps only.", "detailed": " Give a fuller, well-structured answer.",
                 "simple": " Use very simple words."}.get(req.output_style, "")
        extra = ""
        if req.intent == Intent.WRITING:
            extra = " Draft the text they asked for; do not send it anywhere."
        elif req.intent == Intent.PLANNING:
            extra = " Give a short, realistic plan as a list; nothing is scheduled until they ask."
        return PERSONA + style + extra, accept, ""

    def _segments(self, req, system, history, pending, attached, sources) -> list:
        S = ce.Segment
        segs = [S("system", system, label="route instruction")]
        try:
            from ..settings.runtime import verbosity_instruction
            v = verbosity_instruction()
            if v:
                segs.append(S("constraint", v, label="length setting"))
        except Exception:  # noqa: BLE001
            pass
        turns = _plain_turns(history)
        n = len(turns)
        for i, (role, content) in enumerate(turns):
            age = n - i
            if role == "user" and _CONSTRAINT.search(content) and len(content) < 240:
                segs.append(S("constraint", content, label="standing instruction", age=age))
            elif role == "user" and _CORRECTION.search(content) and age <= 6:
                segs.append(S("correction", content, label="correction", age=age))
            else:
                segs.append(S("turn" if age <= 2 else "history", content, role=role, age=age - 1))
        for summary in pending:
            segs.append(S("approval", summary, label="pending approval"))
        for kind, text in attached:
            segs.append(S(kind, text, source="attached context", label="attached"))
        if sources:
            segs.append(S("tool", research.reference_block(sources), protected=True, source="web search",
                          label="search results"))
        if self.recall is not None and ce.should_retrieve_memory(req.text, req.intent):
            try:
                found = self.recall(req.text)
            except Exception:  # noqa: BLE001
                found = ""
            if found:
                segs.append(S("memory", str(found)[:2000], source="memory vault", label="memory"))
        return segs

    def _finish(self, req: BrainRequest, text: str, label: str, sources) -> str:
        from ..agent import action_claims, spoken
        out = _CONF.sub("", text or "").strip()
        out = _OPENERS.sub("", out).strip()
        if action_claims.claims_an_action(out) and not action_claims.asks_for_an_action(req.text):
            out = ("I haven't done anything — that sounded like a question. Say the word if you want it done.")
        out = spoken.trim_trailer(out) or out
        if label:
            out = f"{label}:\n{out}"
        if sources:
            out += research.citation_footer(sources)
        return out


def _confidence_check(result) -> Optional[str]:
    m = _CONF.search(result.text or "")
    if not m:
        return "no confidence line"
    return "the fast model said its confidence was low" if m.group(1).lower() == "low" else None


def _plain_turns(history: list) -> list[tuple[str, str]]:
    out = []
    for m in history or []:
        if m.get("role") not in {"user", "assistant"} or m.get("tool_calls"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = re.sub(r"^\[time: [^\]]*\]\s*", "", content).strip()
        from ..trust import own_words
        if m["role"] == "user":
            content = own_words(content) or content
        out.append((m["role"], content[:2000]))
    return out[-200:]


def _attached_context(text: str) -> list[tuple[str, str]]:
    """Bracketed context the front-ends attach (screen text, a message, a selection) — untrusted."""
    blocks = []
    for m in re.finditer(r"(?ms)^\s*\[([^\]\n]{0,80})\]\s*$(.*?)^\s*\[end of [^\]\n]*\]\s*$", text or ""):
        blocks.append(("attachment", m.group(2).strip()[:6000]))
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]") and len(s) > 40 and not s.lower().startswith("[end of"):
            blocks.append(("screen", s[1:-1][:3000]))
    return blocks


_BRAIN: dict = {"b": None}


def brain() -> DailyBrain:
    if _BRAIN["b"] is None:
        _BRAIN["b"] = DailyBrain()
    return _BRAIN["b"]


def tool_choice(text: str, session: str = "local"):
    """(base_url, key, model) of the first *verified* tool-calling model for this request, or None.

    None when the brain is off, the request is not from the owner, or no model has passed the tool
    probes yet — the agent then keeps its configured model, and the event says so.
    """
    if not enabled():
        return None
    try:
        b = brain()
        req = b.classify(text, session)
        if req.source not in Source.TRUSTED:
            return None
        req.intent = Intent.ACTION
        req.capabilities = set(req.capabilities) | {"tool_calling"}
        registry = b.registry
        decision = router.plan(req, registry)
        ks = b._keystore_or_none()
        for cand in decision.candidates:
            p = registry.providers[cand.provider_id]
            if p.local or p.auth == "none":
                return (p.base_url.rstrip("/"), "", cand.model_id)
            keys = ks.usable(p.id, p.env_var)
            if keys:
                telemetry.record(request_id=req.request_id, source=req.source, intent=req.intent, route="tools",
                                 provider=p.id, model=cand.model_id, status="tool_model_selected")
                return (p.base_url.rstrip("/"), ks.secret(keys[0]), cand.model_id)
        telemetry.record(request_id=req.request_id, source=req.source, intent=req.intent, route="tools",
                         status="tools_unverified_legacy_model")
    except Exception:  # noqa: BLE001
        return None
    return None


async def maybe_answer(text: str, session: str = "local", history: list | None = None,
                       on_delta=None) -> Optional[DailyReply]:
    """The agent's entry point. None means: not mine — carry on with the tool loop."""
    import asyncio
    if not enabled():
        return None
    try:
        from ..approvals import MANAGER
        pending = [a.summary for a in MANAGER.pending(session)]
    except Exception:  # noqa: BLE001
        pending = []
    b = brain()
    if b.recall is None:
        from ..memory.search import recall as vault_recall
        b.recall = lambda q: vault_recall(q, b.config.vault_path, k=3)
    try:
        return await asyncio.to_thread(b.respond, text, session, history=history, on_delta=on_delta,
                                       pending=pending)
    except Exception as exc:  # noqa: BLE001 - the brain must never break a turn; the agent still can
        telemetry.record(route="daily", status=f"error:{type(exc).__name__}")
        return None
