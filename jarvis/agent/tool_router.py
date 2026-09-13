"""Semantic tool retrieval — send the brain the ~14 tools that matter, not all 76.

Why this exists
---------------
Jarvis exposes 76 tools. Handing every one of them to the model on every turn costs ~3.5k tokens
of schema and, far worse, drowns a small local model in distractors: asked "what's my CPU usage?",
qwen2.5:3b would reply "none of the provided functions apply" instead of calling `system_stats`.
The old workaround — truncating every description to 40 characters — made selection worse, because
a name plus a clipped sentence is exactly the signal a small model needs and no longer has.

So: keep the full descriptions, and retrieve. Each turn we score all tools against the user's text
and hand over a short, relevant menu:

    always-on core  +  lexical matches  +  semantic (embedding) matches  ->  top `k`

Ranking is Reciprocal Rank Fusion over the lexical and semantic rankings, so a tool only one of
them likes still surfaces, and a tool both like wins. Embeddings come from Ollama's nomic-embed
(already a dependency of the memory vault) and are cached on disk keyed by a hash of the tool set,
so the ~76 embed calls happen once per tool-set change, not once per turn.

Everything degrades: no Ollama -> lexical only; nothing scores -> the core set plus a stable
alphabetical fill; router disabled (JARVIS_TOOL_ROUTER=0) -> every tool, exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Iterable, Optional

# Tools worth offering on every single turn: the conversational glue (memory, machine, screen,
# web) plus the escape hatch. Anything else has to earn its place through retrieval.
CORE_TOOLS = (
    "recall",
    "system_stats",
    "capture_screen",
    "web_search",
    "run_bash",
    "log_activity",
)

# Hand-written cues for tools whose wording doesn't match how people actually ask. Pure bonus
# signal — a miss here costs nothing, it just falls back to the description text.
SYNONYMS: dict[str, str] = {
    "what_am_i_doing": "what am i doing right now what is on my screen which app am i in am i busy focused window open windows what is playing am i away",
    "system_stats": "cpu usage ram memory gpu temperature battery disk load health machine hot slow",
    "capture_screen": "look at my screen see what i am doing read this screenshot what is on screen",
    "recall": "remember memory you told me earlier note vault what do you know about",
    "whatsapp_send": "text message whatsapp send him her them reply dm",
    "message_person": "text someone about something tell him tell her let them know",
    "whatsapp_inbox": "unread messages who messaged me new texts",
    "find_contact": "phone number contact details of a person lookup",
    "remember_contact": "save this number remember his number store contact",
    "google_agenda": "calendar schedule today meetings what is next appointment",
    "google_email_check": "inbox mail unread email anything important",
    "google_email_send": "send an email compose mail write to",
    "google_calendar_create": "book a meeting schedule an event put it in my calendar",
    "set_timer": "countdown timer remind me in minutes alarm",
    "set_reminder": "remind me at remind me tomorrow reminder",
    "media_control": "play pause skip next track song music spotify",
    "set_volume": "louder quieter volume turn it up turn it down mute",
    "set_brightness": "screen brightness dimmer brighter",
    "lock_screen": "lock the computer secure my laptop",
    "do_not_disturb": "silence notifications focus mode quiet",
    "open_url": "open website go to browse navigate to a site",
    "launch_app": "open the app start program run application",
    "deep_research": "research this thoroughly investigate look deeply into find out everything",
    "web_search": "search the web look up google find online news",
    "who_is_around": "who is nearby anyone here people around presence radar",
    "wifi_scan": "wifi networks around signal access points",
    "bluetooth_scan": "bluetooth devices nearby paired",
    "catch_up": "what did i miss brief me summary of everything update me",
    "read_project": "the code i have open my project vs code workspace repo",
    "join_meet_and_take_notes": "google meet take notes minutes transcribe the call",
    "set_away": "i am going out away mode cover for me heads down",
    "find_and_click": "click the button press that on screen tap the link",
    "type_text": "type this for me write it into the app keyboard input",
    "trigger_automation": "run the workflow n8n automation trigger",
    "phone_mirror": "show my phone mirror screen scrcpy",
    "place_call": "call him ring dial phone someone",
    "instagram_dms": "instagram messages ig dms",
    "conversation_search": "what did we talk about search our chats history",
    "analyze_image": "describe this picture what is in the photo image file",
}

_STOP = frozenset(
    """a an the and or but if then than that this these those is are am was were be been being do does
    did done have has had having i me my mine you your yours he him his she her it its we us our they
    them their to of in on at for with from by as so just can could would should will shall may might
    please now get got make made go going about into over under again very really ok okay hey jarvis
    what whats which who whom whose when where why how much many some any all no not up down out""".split()
)

_CACHE_LOCK = threading.Lock()


def _state_dir() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()


def enabled() -> bool:
    return os.environ.get("JARVIS_TOOL_ROUTER", "1").strip().lower() not in {"0", "false", "no", "off"}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 1]


def _schema_text(schema: dict) -> str:
    """The searchable text for a tool: name words + description + parameter names + synonyms."""
    fn = schema.get("function", {})
    name = fn.get("name", "")
    params = (fn.get("parameters") or {}).get("properties") or {}
    return " ".join(
        [name.replace("_", " "), fn.get("description", ""), " ".join(params), SYNONYMS.get(name, "")]
    ).strip()


def _fingerprint(schemas: Iterable[dict]) -> str:
    blob = json.dumps(
        [(s.get("function", {}).get("name", ""), _schema_text(s)) for s in schemas], sort_keys=True
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# lexical scoring (BM25-lite) — always available, no dependencies              #
# --------------------------------------------------------------------------- #
class _Lexical:
    """BM25 over the tool corpus. Small enough that the whole thing is a few dicts."""

    K1 = 1.4
    B = 0.7

    def __init__(self, docs: dict[str, str]) -> None:
        self.tf: dict[str, dict[str, int]] = {}
        self.length: dict[str, int] = {}
        df: dict[str, int] = {}
        for name, text in docs.items():
            toks = _tokens(text)
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            self.tf[name] = counts
            self.length[name] = max(1, len(toks))
            for t in counts:
                df[t] = df.get(t, 0) + 1
        n = max(1, len(docs))
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.avg_len = sum(self.length.values()) / n

    def rank(self, query: str) -> list[tuple[str, float]]:
        q = _tokens(query)
        if not q:
            return []
        scores: dict[str, float] = {}
        for name, counts in self.tf.items():
            total = 0.0
            for term in q:
                f = counts.get(term)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = f + self.K1 * (1 - self.B + self.B * self.length[name] / self.avg_len)
                total += idf * (f * (self.K1 + 1)) / denom
            if total > 0:
                scores[name] = total
        return sorted(scores.items(), key=lambda kv: -kv[1])


# --------------------------------------------------------------------------- #
# semantic scoring (Ollama embeddings, cached on disk)                         #
# --------------------------------------------------------------------------- #
def _cosine_ranking(query_vec: list[float], vecs: dict[str, list[float]]) -> list[tuple[str, float]]:
    qn = math.sqrt(sum(v * v for v in query_vec)) or 1.0
    out: list[tuple[str, float]] = []
    for name, vec in vecs.items():
        if len(vec) != len(query_vec):
            continue
        dot = sum(a * b for a, b in zip(query_vec, vec))
        vn = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append((name, dot / (qn * vn)))
    out.sort(key=lambda kv: -kv[1])
    return out


class ToolRouter:
    """Scores a tool set against a user utterance. One instance per agent; cheap to keep around."""

    def __init__(self, schemas: list[dict], k: int = 14, embed_model: str = "nomic-embed-text") -> None:
        self.schemas = schemas
        self.k = max(1, int(k))
        self.embed_model = embed_model
        self.by_name = {s.get("function", {}).get("name", ""): s for s in schemas}
        self.docs = {name: _schema_text(s) for name, s in self.by_name.items()}
        self.lexical = _Lexical(self.docs)
        self.fingerprint = _fingerprint(schemas)
        self.cache_path = _state_dir() / f"tool-index-{self.fingerprint}.json"
        self.vectors: dict[str, list[float]] = {}
        self._embed_tried = False
        self._load_cache()

    # -- embedding cache ---------------------------------------------------
    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("model") == self.embed_model:
            vecs = data.get("vectors")
            if isinstance(vecs, dict):
                self.vectors = {k: v for k, v in vecs.items() if k in self.by_name}

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.cache_path.write_text(
                json.dumps({"model": self.embed_model, "vectors": self.vectors}), encoding="utf-8"
            )
            # A new tool set means a new fingerprint; sweep the stale indexes so the dir stays small.
            for old in self.cache_path.parent.glob("tool-index-*.json"):
                if old != self.cache_path:
                    old.unlink(missing_ok=True)
        except OSError:
            pass

    def warm(self) -> int:
        """Embed every tool description once. Returns how many vectors are available."""
        if self._embed_tried and self.vectors:
            return len(self.vectors)
        with _CACHE_LOCK:
            if len(self.vectors) >= len(self.docs):
                return len(self.vectors)
            self._embed_tried = True
            from ..memory import embeddings

            if not embeddings.available(timeout=1.0):
                return len(self.vectors)
            for name, text in self.docs.items():
                if name in self.vectors:
                    continue
                vec = embeddings.embed(text, self.embed_model, timeout=20.0)
                if vec:
                    self.vectors[name] = vec
            self._save_cache()
            return len(self.vectors)

    # Cosine below this is noise, not a match. Embedding models score almost any two English
    # strings around 0.3-0.45, so without a floor "hello" drags in fourteen unrelated tools.
    MIN_SIMILARITY = 0.52

    def _semantic_ranking(self, query: str) -> list[tuple[str, float]]:
        if not self.vectors:
            return []
        from ..memory import embeddings

        vec = embeddings.embed(query, self.embed_model, timeout=8.0)
        if not vec:
            return []
        return [(n, s) for n, s in _cosine_ranking(vec, self.vectors) if s >= self.MIN_SIMILARITY]

    # -- selection ---------------------------------------------------------
    def select(self, query: str, extra: Iterable[str] = (), k: Optional[int] = None) -> list[dict]:
        """Return the schemas to expose for this turn, most relevant first.

        `extra` pins tools the caller knows are in play (e.g. the tool used on the previous turn,
        so a follow-up like "do that again for Tuesday" still sees it).
        """
        k = self.k if k is None else max(1, int(k))
        if not enabled() or len(self.schemas) <= k:
            return list(self.schemas)

        lexical = self.lexical.rank(query)
        semantic = self._semantic_ranking(query)

        # Reciprocal Rank Fusion: a tool ranked well by either signal survives; both, and it wins.
        fused: dict[str, float] = {}
        for ranking, weight in ((lexical, 1.0), (semantic, 1.0)):
            for rank, (name, _score) in enumerate(ranking):
                fused[name] = fused.get(name, 0.0) + weight / (60 + rank + 1)

        chosen: list[str] = []

        def take(name: str) -> None:
            if name in self.by_name and name not in chosen:
                chosen.append(name)

        for name in extra:
            take(name)
        for name in CORE_TOOLS:
            take(name)
        for name, _ in sorted(fused.items(), key=lambda kv: -kv[1]):
            if len(chosen) >= k:
                break
            take(name)

        # Deliberately no filler. If only three tools matched, send three — padding the menu back
        # up to `k` with alphabetically-adjacent tools just rebuilds the distractor pile this whole
        # module exists to remove. A short menu is the point.
        return [self.by_name[n] for n in chosen[:k]]

    def explain(self, query: str, k: Optional[int] = None) -> list[str]:
        """Names only — for diagnostics (`python -m jarvis --tools "..."`)."""
        return [s["function"]["name"] for s in self.select(query, k=k)]
