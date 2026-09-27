"""The Daily Brain: one place that decides which model — if any — answers a request.

``jarvis/brains`` (plural) is the specialist roster: which *instruction* a turn runs under. This
package is the layer underneath it: which *provider and model* a turn may use, why, what happens
when that fails, what context goes with it and what the person is told about it. See
``docs/DAILY_BRAIN.md``.

Modules, in the order a request meets them:

    request      the typed request, capability flags, the route decision record
    understand   language, intent, capabilities, freshness, difficulty — no model call
    privacy      what may leave the machine, and redaction
    study        Class 10 / NCERT-style detection and answer shaping
    registry     providers, models, capabilities, catalogue cache, deprecation aliases
    keys         user-owned API keys: Secret Service storage, rotation, quarantine
    adapters     the wire: one OpenAI-compatible adapter (Groq, Gemini, Ollama, OpenAI, ...)
    router       tiers and profiles → an ordered list of candidates, with reasons
    executor     bounded fallback over candidates and keys; honest notices
    contextengine token budgets, relevance, compaction with protected categories
    cache        scoped, expiring caches that refuse secrets
    research     freshness-sensitive questions answered from retrieved sources
    toolcheck    deterministic validation of model-written tool calls; capability probes
    telemetry    routing events without private content, bounded retention
    daily        the unified daily-assistant route used by the agent
    api          the /brain HTTP API used by the overlay's Brain tab
"""
