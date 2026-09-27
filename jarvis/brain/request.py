"""What a request is, what it needs, and what was decided about it.

Everything here is data. Deciding is ``understand`` and ``router``; doing is ``executor``. Keeping
the record separate is what lets a reply say honestly which model answered it: the decision is
written down before anything is called, and the outcome is written onto the same object.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


class Cap:
    """Capability flags. Strings rather than an Enum so a provider record, a JSON settings file
    and a 3D-branch declaration (``image_to_3d``) can all name one without importing this."""

    CHAT = "chat"
    REASONING = "reasoning"
    TOOLS = "tool_calling"
    VISION = "vision"
    OCR = "ocr"
    CODE = "code"
    STRUCTURED = "structured_output"
    STREAMING = "streaming"
    LONG_CONTEXT = "long_context"
    MULTILINGUAL = "multilingual"
    HINDI = "hindi"
    HINGLISH = "hinglish"
    RESEARCH = "research"
    LOCAL = "local_private"
    LOW_LATENCY = "low_latency"
    HIGH_ACCURACY = "high_accuracy"
    EMBEDDING = "embedding"
    # Declared by the Study Companion and 3D Studio (capability.py). The ones a local engine can
    # serve are in capability.ENGINES; the rest need a model that has them.
    SOURCE_GROUNDING = "source_grounding"
    SEGMENTATION = "image_segmentation"
    SCENE_PLANNING = "structured_scene_planning"
    IMAGE_TO_3D = "image_to_3d"
    REASONING_3D = "3d_reasoning"
    BLENDER = "blender_editing"
    COMPARE = "multimodal_comparison"

    ALL = frozenset({CHAT, REASONING, TOOLS, VISION, OCR, CODE, STRUCTURED, STREAMING, LONG_CONTEXT,
                     MULTILINGUAL, HINDI, HINGLISH, RESEARCH, LOCAL, LOW_LATENCY, HIGH_ACCURACY,
                     EMBEDDING, SOURCE_GROUNDING, SEGMENTATION, SCENE_PLANNING, IMAGE_TO_3D, REASONING_3D,
                     BLENDER, COMPARE})

    # Capabilities a model must have *verified* (by a probe), not merely declared, before a route
    # that needs them will use it. A text model that writes JSON-looking prose is not tool-capable.
    # No probe exists yet for image-to-3D, segmentation or image comparison, so no model has them:
    # those requests are served by local engines or refused honestly.
    MUST_VERIFY = frozenset({TOOLS, VISION, IMAGE_TO_3D, SEGMENTATION, COMPARE})


class Source:
    VOICE, OVERLAY, CLI, WEB, SYSTEM = "voice", "overlay", "cli", "web", "system"
    MESSAGING, EMAIL, BROWSER, OCR = "messaging", "email", "browser", "ocr"
    TRUSTED = frozenset({VOICE, OVERLAY, CLI, WEB, SYSTEM})

    @staticmethod
    def from_session(session_id: str) -> str:
        """The existing session ids ("local", "voice", "whatsapp", ...) as a source category."""
        s = (session_id or "").strip().lower()
        if s in {"local", "overlay", "hud"}:
            return Source.OVERLAY
        if s in {"voice"}:
            return Source.VOICE
        if s in {"cli", "terminal"}:
            return Source.CLI
        if s in {"web"}:
            return Source.WEB
        if s in {"system", "routine"}:
            return Source.SYSTEM
        if s in {"email", "gmail"}:
            return Source.EMAIL
        if s in {"browser", "screen", "document"}:
            return Source.BROWSER
        if s in {"ocr"}:
            return Source.OCR
        return Source.MESSAGING      # whatsapp, telegram, sms, away, unknown: untrusted


class Privacy:
    PUBLIC, PERSONAL, SENSITIVE, SECRET = "public", "personal", "sensitive", "secret"
    ORDER = (PUBLIC, PERSONAL, SENSITIVE, SECRET)


class Intent:
    DETERMINISTIC = "deterministic"   # handled by a fixed parser, no model
    CONVERSATION = "conversation"     # a question or chat — answered in words
    STUDY = "study"                   # explanation / exam answer / quiz
    RESEARCH = "research"             # current or changing information
    VISION = "vision"                 # about an image, the screen or the camera
    ACTION = "action"                 # changes something: needs tools and maybe approval
    CODE = "code"
    WRITING = "writing"               # help drafting (drafting is not sending)
    PLANNING = "planning"
    MEMORY = "memory"                 # "remember this", "what was I doing"


class Tier:
    DETERMINISTIC, LOCAL_FAST, CLOUD_FAST, STRONG = 0, 1, 2, 3
    NAMES = {0: "deterministic", 1: "local-fast", 2: "cloud-fast", 3: "strong"}


@dataclass
class BrainRequest:
    text: str
    source: str = Source.OVERLAY
    authenticated: bool = True
    session_id: str = "local"
    images: list = field(default_factory=list)          # paths or data URIs; never logged
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    correlation_id: str = ""
    language: str = "en"                                # en / hi / hinglish
    privacy: str = Privacy.PUBLIC
    privacy_reasons: list = field(default_factory=list)
    intent: str = Intent.CONVERSATION
    capabilities: set = field(default_factory=lambda: {Cap.CHAT})
    latency: str = "normal"                             # fast / normal / relaxed
    quality: str = "normal"                             # normal / high
    cost: str = "low"                                   # free / low / any
    offline_required: bool = False
    tool_permission: str = "none"                       # none / read / act
    side_effect_risk: str = "none"                      # none / low / high
    context_budget: int = 6000                          # tokens for everything but the reply
    output_style: str = "concise"                       # concise / detailed / steps / exam / quiz
    output_tokens: int = 600
    fallback_policy: str = "honest"                     # honest / never_weaker / any
    deadline_s: float = 45.0
    fresh: bool = False                                 # answer depends on current information
    difficulty: float = 0.0                             # 0..1 measured estimate
    study: Optional[dict] = None                        # study.StudyRequest as dict
    created: float = field(default_factory=time.time)

    @property
    def trusted(self) -> bool:
        return self.authenticated and self.source in Source.TRUSTED

    def safe_summary(self) -> dict:
        """The request without its words or images — what telemetry may keep."""
        return {"request_id": self.request_id, "source": self.source, "intent": self.intent,
                "language": self.language, "privacy": self.privacy,
                "capabilities": sorted(self.capabilities), "fresh": self.fresh,
                "difficulty": round(self.difficulty, 2), "chars": len(self.text or ""),
                "images": len(self.images)}


@dataclass
class Candidate:
    provider_id: str
    model_id: str
    tier: int
    reason: str
    local: bool = False
    lost: list = field(default_factory=list)            # capabilities this one lacks vs the ideal


@dataclass
class RouteDecision:
    """Everything a reply must be honest about."""

    request_id: str
    route: str                                          # deterministic / chat / study / research / vision / tools / refuse / setup
    tier: int
    requested: str = ""                                 # "cloud-fast via gemini" — what the policy wanted first
    candidates: list = field(default_factory=list)      # [Candidate]
    reasons: list = field(default_factory=list)
    selected_provider: str = ""
    selected_model: str = ""
    selected_tier: Optional[int] = None
    fallback: bool = False
    fallback_reasons: list = field(default_factory=list)   # ["gemini: rate_limited"]
    capability_lost: list = field(default_factory=list)
    quality_reduced: bool = False
    refused: str = ""                                   # why no model was used although one was needed
    tokens_in: int = 0
    tokens_out: int = 0
    context_tokens_before: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier_name"] = Tier.NAMES.get(self.tier, str(self.tier))
        return d
