"""Providers and models: what exists, what it can do, and how much of that has been checked.

One registry for every model call Jarvis makes. It is built from three sources:

* the existing ``Config`` (``.env``): Groq, Gemini and Ollama as they are configured today, so an
  install that has never opened the Brain tab keeps working exactly as before;
* ``brain.json`` (user settings, ``~/.config/jarvis/brain.json``): enabled flags, priorities,
  base URLs, manually entered model ids, routing profile, privacy mode, limits, key *metadata*;
* ``brain-state.json`` (runtime state, ``$JARVIS_STATE_DIR``): catalogue cache, probe results,
  measured latency, key backoff/quarantine. Never secrets.

Capabilities come in two strengths. *Declared* is what the provider or this table says the model
can do; it is never trusted for tools or vision (``Cap.MUST_VERIFY``). *Verified* is what a probe
in ``toolcheck`` actually observed, with a timestamp. A route that needs tool calling or vision
only uses a model whose capability is verified.

The per-model circuit breaker is ``jarvis.providers`` — the same state file the rest of Jarvis
already reads — so a model paused here is paused for the legacy paths too, and vice versa.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .. import providers as legacy
from .request import Cap, Tier

# --------------------------------------------------------------------------- paths

def settings_path() -> Path:
    raw = os.environ.get("JARVIS_BRAIN_CONFIG")
    if raw:
        return Path(raw).expanduser()
    return Path(os.environ.get("JARVIS_CONFIG_DIR", "~/.config/jarvis")).expanduser() / "brain.json"


def state_path() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser() / "brain-state.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- known models
# Declared capabilities, deliberately conservative. Matched by regex on the model id, first match
# wins. ``tier`` is the default role; ``hindi`` means measured-good Hindi, not "has seen Hindi".
# Nothing here is marketing: a row says what this repository has observed or what the provider
# documents, and tools/vision still need a probe before any route relies on them.
KNOWN = [
    # pattern, tier, declared caps, context window, default out, price in/out per 1M (None=unknown)
    (r"gpt-oss-120b", Tier.STRONG, {"chat", "reasoning", "tool_calling", "structured_output", "streaming",
                                    "multilingual", "hindi", "hinglish", "code", "high_accuracy", "long_context"},
     131072, 1024, None),
    (r"gpt-oss-20b", Tier.CLOUD_FAST, {"chat", "reasoning", "tool_calling", "structured_output", "streaming",
                                       "multilingual", "hinglish", "code", "low_latency"}, 131072, 800, None),
    (r"qwen/qwen3", Tier.CLOUD_FAST, {"chat", "reasoning", "tool_calling", "structured_output", "streaming",
                                      "multilingual", "hindi", "hinglish", "code"}, 131072, 800, None),
    (r"llama-3\.3-70b|llama-4", Tier.CLOUD_FAST, {"chat", "tool_calling", "structured_output", "streaming",
                                                  "multilingual", "hinglish", "code", "low_latency"}, 131072, 800, None),
    (r"gemini-[\d.]+-pro", Tier.STRONG, {"chat", "reasoning", "tool_calling", "vision", "ocr", "structured_output",
                                         "streaming", "multilingual", "hindi", "hinglish", "code", "long_context",
                                         "high_accuracy"}, 1000000, 1200, None),
    (r"gemini-[\d.]+-flash", Tier.CLOUD_FAST, {"chat", "reasoning", "tool_calling", "vision", "ocr",
                                               "structured_output", "streaming", "multilingual", "hindi", "hinglish",
                                               "code", "long_context", "low_latency"}, 1000000, 800, None),
    (r"^gpt-[45]|^o\d", Tier.STRONG, {"chat", "reasoning", "tool_calling", "vision", "structured_output",
                                     "streaming", "multilingual", "hindi", "hinglish", "code", "long_context"},
     128000, 1000, None),
    (r"moondream|llava|llama3\.2-vision|minicpm-v|qwen2\.5vl|qwen2\.5-vl", Tier.LOCAL_FAST,
     {"vision", "local_private"}, 4096, 300, 0.0),
    (r"nomic-embed|mxbai-embed|bge-|all-minilm", Tier.LOCAL_FAST, {"embedding", "local_private"}, 2048, 0, 0.0),
    (r"qwen2\.5:3b|qwen2\.5:1\.5b|llama3\.2:3b|phi\d|gemma\d?:2b", Tier.LOCAL_FAST,
     {"chat", "tool_calling", "structured_output", "streaming", "local_private", "low_latency"}, 32768, 400, 0.0),
    (r"qwen3(?:\.5)?:\d+b|qwen2\.5:7b|llama3\.1:8b|gemma\d?:[4-9]b|mistral", Tier.LOCAL_FAST,
     {"chat", "tool_calling", "structured_output", "streaming", "local_private", "multilingual"}, 32768, 500, 0.0),
]

# Model ids providers have removed, and what replaced them. Offered as a suggestion only: the
# configured id is never rewritten without the owner saying yes (``approve_replacement``).
MIGRATION_ALIASES = {
    "llama3-70b-8192": "llama-3.3-70b-versatile",
    "llama3-8b-8192": "llama-3.1-8b-instant",
    "llama-3.1-70b-versatile": "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768": "openai/gpt-oss-20b",
    "gemma2-9b-it": "openai/gpt-oss-20b",
    "gemma-7b-it": "openai/gpt-oss-20b",
    "gemini-1.5-flash": legacy.DEFAULT_GEMINI_MODEL,
    "gemini-1.5-pro": legacy.DEFAULT_GEMINI_MODEL,
    "gemini-2.0-flash": legacy.DEFAULT_GEMINI_MODEL,
}

# Provider templates the owner can add from the Brain tab. Only OpenAI-compatible endpoints: that
# is the one adapter this repository has, and listing a provider there is no adapter for would be
# a promise the router cannot keep.
TEMPLATES = {
    "openai": {"display_name": "OpenAI", "base_url": "https://api.openai.com/v1", "env": "OPENAI_API_KEY",
               "key_hint": "sk-…", "local": False},
    "openrouter": {"display_name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "env": "OPENROUTER_API_KEY", "key_hint": "sk-or-…", "local": False},
    "llamacpp": {"display_name": "llama.cpp server", "base_url": "http://127.0.0.1:8080/v1", "env": "",
                 "key_hint": "", "local": True},
    "custom": {"display_name": "OpenAI-compatible endpoint", "base_url": "", "env": "", "key_hint": "",
               "local": False},
}


def declared(model_id: str) -> tuple[int, set, int, int, Optional[float]]:
    for pattern, tier, caps, ctx, out, price in KNOWN:
        if re.search(pattern, model_id or "", re.I):
            return tier, set(caps), ctx, out, price
    return Tier.CLOUD_FAST, {"chat"}, 8192, 500, None


# --------------------------------------------------------------------------- records

@dataclass
class ModelRecord:
    id: str
    provider: str
    tier: int
    declared: set
    verified: dict = field(default_factory=dict)       # cap -> {"ok": bool, "at": ts, "score": str}
    input_modalities: list = field(default_factory=lambda: ["text"])
    output_modalities: list = field(default_factory=lambda: ["text"])
    context_window: int = 8192
    default_output: int = 500
    local_resources: dict = field(default_factory=dict)   # {"size_gb":…, "vram_gb":…}
    first_token_ms: Optional[float] = None
    completion_ms: Optional[float] = None
    price_per_mtok: Optional[float] = None
    health: str = "unknown"                            # ok / paused:<kind> / unavailable / unknown
    deprecated: bool = False
    replacement: str = ""
    last_validated: Optional[float] = None
    manual: bool = False
    available: bool = True                             # False when the catalogue no longer lists it

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.id}"

    def has(self, cap: str) -> bool:
        """Usable for this capability: verified when it must be, declared otherwise."""
        if cap in Cap.MUST_VERIFY:
            v = self.verified.get(cap)
            return bool(v and v.get("ok"))
        if cap == Cap.LOCAL:
            return "local_private" in self.declared
        return cap in self.declared or bool(self.verified.get(cap, {}).get("ok"))

    def to_public(self) -> dict:
        d = asdict(self)
        d["declared"] = sorted(self.declared)
        d["ref"] = self.ref
        d["tier_name"] = Tier.NAMES.get(self.tier)
        return d


@dataclass
class ProviderRecord:
    id: str
    display_name: str
    adapter: str                                       # openai_compatible / ollama / browser
    base_url: str
    auth: str                                          # bearer / none / browser_login
    local: bool
    privacy: str                                       # "stays on this machine" / "sent to <name>"
    enabled: bool = True
    priority: int = 50
    env_var: str = ""
    routable: bool = True
    models: dict = field(default_factory=dict)         # id -> ModelRecord
    health: str = "unknown"
    last_success: Optional[float] = None
    last_failure: str = ""
    backoff_until: Optional[float] = None
    rate_limited_until: Optional[float] = None
    catalogue_at: Optional[float] = None
    key_hint: str = ""

    def to_public(self, keys: list | None = None) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "models"}
        d["models"] = [m.to_public() for m in self.models.values()]
        d["keys"] = keys or []
        return d


# --------------------------------------------------------------------------- settings

DEFAULT_SETTINGS = {
    "version": 1,
    "profile": "balanced",
    "privacy": {"mode": "allow_cloud", "allow_screenshots": True, "vision_providers": None},
    "limits": {"max_cost_usd": 0.05, "max_latency_s": 45, "cloud_escalation": True, "external_vision": True},
    "providers": {},          # id -> {enabled, priority, base_url, display_name, template, models: [...]}
    "keys": {},               # provider id -> [key metadata]; secrets live in the keyring
    "routing": {},            # profile -> {capability: ["provider/model", ...]}
    "replacements": {},       # old model ref -> approved new model id
    "setup_complete": False,
}


class BrainSettings:
    def __init__(self, data: dict | None = None, path: Path | None = None):
        self.path = path or settings_path()
        base = json.loads(json.dumps(DEFAULT_SETTINGS))
        for k, v in (data if data is not None else _read_json(self.path)).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k].update(v)
            else:
                base[k] = v
        self.data = base

    def __getitem__(self, key):
        return self.data[key]

    def save(self) -> None:
        # Guard rail: nothing that looks like a secret is ever written to the settings file.
        from .privacy import find_secrets
        text = json.dumps(self.data)
        if find_secrets(text):
            raise ValueError("refusing to write something secret-looking into brain.json")
        _write_json(self.path, self.data)


class BrainState:
    """Runtime observations. Lost without harm: everything here is re-measured."""

    def __init__(self, path: Path | None = None):
        self.path = path or state_path()
        self.data = _read_json(self.path)
        self.data.setdefault("catalogue", {})     # provider -> {"at": ts, "models": [...]}
        self.data.setdefault("verified", {})      # "provider/model" -> {cap: {...}}
        self.data.setdefault("latency", {})       # "provider/model" -> {"first_ms":…, "total_ms":…, "n":…}
        self.data.setdefault("keys", {})          # key id -> {"quarantined": kind, "until": ts, …}
        self.data.setdefault("providers", {})     # provider -> {"last_success":…, "last_failure":…}

    def save(self) -> None:
        try:
            _write_json(self.path, self.data)
        except OSError:
            pass

    def note_latency(self, ref: str, first_ms: float | None, total_ms: float) -> None:
        row = self.data["latency"].setdefault(ref, {"n": 0})
        n = min(int(row.get("n", 0)), 19)          # an average over roughly the last twenty calls
        for key, val in (("first_ms", first_ms), ("total_ms", total_ms)):
            if val is None:
                continue
            old = row.get(key)
            row[key] = round(val if old is None else (old * n + val) / (n + 1), 1)
        row["n"] = n + 1

    def verify(self, ref: str, cap: str, ok: bool, score: str = "") -> None:
        self.data["verified"].setdefault(ref, {})[cap] = {"ok": bool(ok), "at": time.time(), "score": score}


# --------------------------------------------------------------------------- the registry

CATALOGUE_TTL_S = 24 * 3600


class Registry:
    def __init__(self, config=None, settings: BrainSettings | None = None, state: BrainState | None = None):
        if config is None:
            from ..config import CONFIG as config
        self.config = config
        self.settings = settings or BrainSettings()
        self.state = state or BrainState()
        self.providers: dict[str, ProviderRecord] = {}
        self._build()

    # -- construction ----------------------------------------------------------------------
    def _build(self) -> None:
        c = self.config
        user = self.settings["providers"]
        self._add(ProviderRecord("ollama", "Ollama (this computer)", "ollama", getattr(c, "ollama_base", ""),
                                 "none", True, "stays on this machine", priority=90),
                  [getattr(c, "ollama_model", legacy.DEFAULT_OLLAMA_MODEL),
                   getattr(c, "ollama_vision_model", ""), "nomic-embed-text"])
        if getattr(c, "groq_api_key", "") or "groq" in user or self._has_keys("groq"):
            self._add(ProviderRecord("groq", "Groq", "openai_compatible", legacy.GROQ_URL, "bearer", False,
                                     "sent to Groq", priority=20, env_var="GROQ_API_KEY", key_hint="gsk_…"),
                      [*legacy.OPEN_STRONG, getattr(c, "groq_model", ""), legacy.DEFAULT_GROQ_FALLBACK])
        if getattr(c, "gemini_api_key", "") or "gemini" in user or self._has_keys("gemini"):
            self._add(ProviderRecord("gemini", "Google Gemini", "openai_compatible", legacy.GEMINI_URL, "bearer",
                                     False, "sent to Google", priority=30, env_var="GEMINI_API_KEY",
                                     key_hint="AIza…"),
                      [getattr(c, "gemini_model", legacy.DEFAULT_GEMINI_MODEL)])
        for pid, row in user.items():
            if pid in self.providers or not isinstance(row, dict):
                continue
            tmpl = TEMPLATES.get(row.get("template", "custom"), TEMPLATES["custom"])
            local = bool(row.get("local", tmpl["local"]))
            name = row.get("display_name") or tmpl["display_name"]
            self._add(ProviderRecord(pid, name, "openai_compatible", row.get("base_url") or tmpl["base_url"],
                                     "none" if local and not self._has_keys(pid) else "bearer", local,
                                     "stays on this machine" if local else f"sent to {name}",
                                     priority=int(row.get("priority", 60)), env_var=tmpl.get("env", ""),
                                     key_hint=tmpl.get("key_hint", "")), [])
        # User overrides last: enabled, priority, manual model ids, base URL for compatible ones.
        for pid, row in user.items():
            p = self.providers.get(pid)
            if not p or not isinstance(row, dict):
                continue
            p.enabled = bool(row.get("enabled", True))
            p.priority = int(row.get("priority", p.priority))
            if row.get("base_url") and p.adapter == "openai_compatible" and pid not in {"groq", "gemini"}:
                p.base_url = row["base_url"]
            for mid in row.get("models", []) or []:
                self._model(p, mid, manual=True)
        self._apply_state()

    def _has_keys(self, pid: str) -> bool:
        return bool(self.settings["keys"].get(pid))

    def _add(self, provider: ProviderRecord, model_ids) -> None:
        self.providers[provider.id] = provider
        for mid in model_ids:
            if mid:
                self._model(provider, mid)

    def _model(self, provider: ProviderRecord, mid: str, manual: bool = False) -> ModelRecord:
        mid = mid.strip()
        if mid in provider.models:
            provider.models[mid].manual = provider.models[mid].manual or manual
            return provider.models[mid]
        tier, caps, ctx, out, price = declared(mid)
        if provider.local:
            caps.add("local_private")
            tier = Tier.LOCAL_FAST
        else:
            caps.discard("local_private")
        rec = ModelRecord(mid, provider.id, tier, caps, context_window=ctx, default_output=out,
                          price_per_mtok=price, manual=manual)
        if "vision" in caps:
            rec.input_modalities = ["text", "image"]
        if mid in MIGRATION_ALIASES:
            rec.deprecated, rec.replacement = True, MIGRATION_ALIASES[mid]
        provider.models[mid] = rec
        return rec

    def _apply_state(self) -> None:
        now = time.time()
        for p in self.providers.values():
            cat = self.state.data["catalogue"].get(p.id)
            if cat:
                p.catalogue_at = cat.get("at")
                listed = set(cat.get("models", []))
                for mid, m in p.models.items():
                    # A model the provider no longer lists is unavailable — but only when the
                    # catalogue is fresh; a day-old list is not evidence of anything.
                    if listed and now - (p.catalogue_at or 0) < CATALOGUE_TTL_S * 7 and not _listed(mid, listed):
                        m.available, m.health = False, "unavailable"
            pst = self.state.data["providers"].get(p.id, {})
            p.last_success, p.last_failure = pst.get("last_success"), pst.get("last_failure", "")
            for m in p.models.values():
                m.verified = dict(self.state.data["verified"].get(m.ref, {}))
                if m.verified:
                    m.last_validated = max(v.get("at", 0) for v in m.verified.values())
                lat = self.state.data["latency"].get(m.ref, {})
                m.first_token_ms, m.completion_ms = lat.get("first_ms"), lat.get("total_ms")
                held = legacy.blocked(legacy.Provider(p.id, p.base_url, "", m.id, "strong"), now)
                if held:
                    m.health = f"paused:{held.get('kind')}"
                elif m.available and m.health == "unknown" and (m.verified or m.completion_ms):
                    m.health = "ok"
            healths = {m.health for m in p.models.values()}
            p.health = ("ok" if "ok" in healths else "degraded" if any(h.startswith("paused") for h in healths)
                        else "unknown")
            if not p.enabled:
                p.health = "disabled"

    # -- queries ---------------------------------------------------------------------------
    def model(self, ref: str) -> Optional[ModelRecord]:
        pid, _, mid = ref.partition("/")
        p = self.providers.get(pid)
        return p.models.get(mid) if p else None

    def all_models(self) -> list[ModelRecord]:
        return [m for p in self.providers.values() for m in p.models.values()]

    def usable(self, model: ModelRecord, now: float | None = None) -> tuple[bool, str]:
        p = self.providers[model.provider]
        if not p.enabled:
            return False, "provider disabled"
        if not p.routable:
            return False, "not routable"
        if not model.available:
            return False, "model no longer offered by the provider"
        held = legacy.blocked(legacy.Provider(p.id, p.base_url, "", model.id, "strong"), now)
        if held:
            return False, f"paused ({held.get('kind')})"
        return True, ""

    # -- catalogue -------------------------------------------------------------------------
    def record_catalogue(self, pid: str, model_ids: list[str]) -> list[str]:
        """Store a provider's model list; returns configured models it no longer lists."""
        self.state.data["catalogue"][pid] = {"at": time.time(), "models": sorted(set(model_ids))}
        self.state.save()
        p = self.providers[pid]
        p.catalogue_at = time.time()
        missing = []
        for mid, m in p.models.items():
            if not _listed(mid, set(model_ids)):
                m.available, m.health = False, "unavailable"
                missing.append(mid)
            else:
                m.available = True
        return missing

    def catalogue_stale(self, pid: str) -> bool:
        at = self.state.data["catalogue"].get(pid, {}).get("at")
        return not at or time.time() - at > CATALOGUE_TTL_S

    def discovered(self, pid: str) -> list[str]:
        return list(self.state.data["catalogue"].get(pid, {}).get("models", []))

    def suggest_replacement(self, ref: str) -> str:
        """A replacement id to *offer* for a removed model — never applied automatically."""
        m = self.model(ref)
        if m is None:
            return ""
        if m.replacement:
            return m.replacement
        listed = self.discovered(m.provider)
        family = re.split(r"[-:/]", m.id)[0]
        same = [x for x in listed if x.startswith(family) and not re.search(r"whisper|tts|guard|embed", x)]
        return same[0] if same else ""

    def approve_replacement(self, ref: str, new_id: str) -> None:
        """The owner said yes: the configured model is replaced from now on."""
        pid, _, old = ref.partition("/")
        row = self.settings["providers"].setdefault(pid, {})
        models = [x for x in row.get("models", []) if x != old]
        models.append(new_id)
        row["models"] = models
        self.settings["replacements"][ref] = new_id
        self.settings.save()
        p = self.providers[pid]
        p.models.pop(old, None)
        self._model(p, new_id, manual=True)


def _listed(mid: str, listed: set) -> bool:
    mid = mid.removesuffix(":latest")
    listed = {x.removesuffix(":latest") for x in listed}
    return any(x == mid or x.endswith("/" + mid) or x == "models/" + mid or mid.endswith("/" + x)
               for x in listed)
