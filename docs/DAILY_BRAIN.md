# The Daily Brain

One subsystem decides which model — if any — answers a request, with what context, and what the
person is told about it. Code: `jarvis/brain/`. UI: the overlay's **Brain** tab (Alt+5).
Branch `feat/jarvis-daily-brain`.

`jarvis/brains/` (plural) is unchanged: the specialist roster decides which *instruction* a turn
runs under. `jarvis/brain/` decides which *provider and model* a turn may use.

## What was there before (audit, 2026-09-27)

- Two provider lists: `providers.configured()` (strong-first list for teaching) and
  `Config.llm_params()` (the one brain), plus direct `OpenAI(...)` clients built from
  `llm_params()` in `webserver._contacts_ingest`, `agent/omnicore.py`, `away_mode/engine.py`,
  `integrations/meet_bot.py`, `agent/ai_researcher.py` (hard-coded Groq), and a native Gemini
  call in `vision/analyze.py`.
- Every non-deterministic turn went through the tool loop (`GroqAgent.send`): the full standing
  prompt, ~10 routed tool schemas and the history, on one fixed model — for "why is the sky blue?"
  as much as for "message Papa".
- Fallback: 429 → `gpt-oss-20b` → wait → local `qwen2.5:3b`, announced only in the tool log.
- Keys: `.env` only, one per provider. No capability model; "vision" was `brain == "gemini"`.
- Kept and reused: the shared circuit breaker (`providers.py`), failure classes, tool-call
  validation (`agent/tool_contract.py`), the action gate and false-claim checks
  (`agent/gate.py`, `agent/action_claims.py`), trust boundaries (`trust.py`), approvals.

## Request path now

```
utterance ─► jarvis.commands.handle          Tier 0: deterministic parsers, approvals, settings,
                                              away mode, open X, media, timers … no model, ever
          ─► GroqAgent.send ─► gate checks
          ─► brain.daily.respond()           conversation / writing / planning / code / study /
                │                             research / vision (with an image)
                │   understand → privacy → router.plan → contextengine.build → executor.execute
                │   → honest notice + answer          (no tool schemas, no side effects)
                └─ None for action / memory
          ─► tool loop on brain.daily.tool_choice()  (a probe-verified tool model when one exists)
             → tool_contract validation → approvals pipeline (unchanged)
llm.complete(strength=…) ─► the same router/executor (teaching, video explanations, flow)
```

Set `JARVIS_DAILY_BRAIN=0` to return to the previous behaviour exactly.

## Request model and capabilities

`request.BrainRequest`: request id, correlation id, source (voice, overlay, cli, web, system,
messaging, email, browser, ocr), authenticated, text + images, language (en / hi / hinglish),
privacy level and reasons, intent, required capabilities, latency/quality/cost preference,
offline requirement, tool permission, side-effect risk, context budget, output style and output
token budget, fallback policy, deadline, freshness, measured difficulty, study details.

Capabilities (`request.Cap`): chat, reasoning, tool_calling, vision, ocr, code,
structured_output, streaming, long_context, multilingual, hindi, hinglish, research,
local_private, low_latency, high_accuracy, embedding. Strings, so a provider record, a JSON file
or another branch (e.g. `image_to_3d`) can declare one without importing anything.

**Declared vs verified.** A model's declared capabilities come from `registry.KNOWN`
(conservative) or the provider. `tool_calling` and `vision` are in `Cap.MUST_VERIFY`: a route that
needs them only uses a model that passed the probes in `toolcheck`, recorded with a timestamp.

Untrusted sources (messaging, email, browser, OCR, unknown sessions) can supply text but never
get tool capability, and bracketed/fenced content inside a trusted turn is reference data only.

## Routing hierarchy

| Tier | What | Used for |
|---|---|---|
| 0 deterministic | `jarvis.commands` | known apps/sites, media, timers, settings, approvals, contacts, notifications, dictation, away mode, repair status |
| 1 local-fast | Ollama | private/secret input, offline, the `fast`/`private` profiles; never for study, research, vision or hard reasoning unless allowed |
| 2 cloud-fast | Groq gpt-oss-20b, Gemini flash … | everyday questions, study explanations, research answers, tool selection, screen questions |
| 3 strong | gpt-oss-120b, Gemini pro … | measured difficulty ≥ 0.6, the `best`/`study`/`coding` profiles, or a fast answer that failed its check |

**Measured escalation**: difficulty is counted (hard-task markers, constraints, numbers,
conditionals, length), not guessed from one keyword. For reasoning-shaped questions the fast
model ends with `CONFIDENCE: high|low`; low or missing → the next *higher* tier is asked once
with the same compact context. For N-mark answers, an answer outside the marks band escalates
once. Escalation is recorded, not announced.

**Profiles**: fast, balanced (default), best, private, study, coding, vision. The owner can pin a
per-route order per profile (Brain → Routing), set a cost and latency ceiling, disallow cloud
escalation and disallow cloud vision.

## Providers and adapters

Registry (`registry.py`): Ollama (always), Groq and Gemini (when a key exists in `.env` or the
keyring), and owner-added OpenAI-compatible endpoints from templates — OpenAI, OpenRouter,
llama.cpp server, custom. No provider without a working adapter is listed.

**Adapter contract** (`adapters.py`):

```
chat(model, messages, key, *, tools=None, max_tokens, temperature, timeout,
     json_mode=False, on_delta=None) -> ChatResult(text, tool_calls, prompt_tokens,
     completion_tokens, finish, first_token_ms, total_ms, native_tool_calls)
list_models(key, timeout) -> [model ids]
```

Every failure is a `ProviderError(kind, detail, retry_after, status)`; `detail` is redacted.
Kinds: auth_failed, permission_denied, model_not_found, rate_limited, quota_exhausted,
provider_outage, timeout, network, malformed, bad_request, other. `OllamaAdapter` adds
installed/loaded/load/unload/delete. Tests swap `adapters.TRANSPORT` for a fake server.

Files: `~/.config/jarvis/brain.json` (settings; refuses to save anything secret-looking) and
`$JARVIS_STATE_DIR/brain-state.json` (catalogue cache, probe results, latency, key backoff).
The per-model breaker stays `provider-health.json`, shared with the legacy paths.

## Keys

- Stored in the Secret Service (gnome-keyring here) via D-Bus (`dbus_next`), one item per key.
  Without a keyring: a 0600 file, labelled "file (not encrypted)" in the UI.
- `.env` keys appear read-only. **Move to keyring** copies the value and disables the env entry;
  `.env` is never edited.
- The API never returns a key or any part of one: id, label, priority, enabled, storage, status
  and a 6-character fingerprint of a hash. Key bodies are read as raw JSON so a validation error
  cannot echo them. Only the local machine (no phone token, no proxy headers, trusted Origin)
  can change anything.
- Rotation only for: owner priority, 401/403 (quarantined until tested or replaced), 429 (backoff
  for `Retry-After` or the provider's stated delay), reported quota exhaustion (6 h), and
  round-robin across equal-priority keys when enabled. A network failure changes nothing.
- Budget per request: ≤ 4 provider calls, ≤ 2 keys per model, within the deadline.
- Delete is two steps: a confirmation token valid for two minutes.

## Honest fallback

`executor.BrainResult.notice` is the one sentence the person hears, only when it matters:

- "Google Gemini is unavailable (it's down), so I used the configured Groq fallback."
- "I'm offline. I can still control the computer, but this answer is using the smaller local model."
- "No vision-capable model is available, so I can't reliably interpret that image."
- "I couldn't get an answer from any model right now (Groq: its key was rejected; …)."

Offline is detected from a real network error and remembered 30 s, so later cloud candidates are
skipped instead of timing out one by one. A local connection failure pauses the local model; it
does not mark the machine offline. A removed model is paused and a replacement *suggested*;
the configured id changes only when the owner approves it (`/brain/models/replace`).
The tool loop's own rate-limit → local fallback now says so in the reply.

## Privacy routing

`privacy.classify`: public / personal / sensitive (health, money, private messages, contacts,
email contents, screenshots, incoming messages) / secret (passwords, OTPs, API keys, JWTs, card
numbers passing Luhn, Aadhaar, PAN). Modes: allow_cloud (default), prefer_local,
ask_before_cloud, always_local; plus "screenshots may be uploaded" and a per-provider vision
allow-list. Secrets never reach a cloud model under any mode; with no local model the answer
says so instead. Telemetry, caches and the journal get `privacy.redact`.

## Context engine and token budget

`contextengine.build(current, segments, budget)` — categories: system, constraint, correction,
approval, task, summary, history, turn, memory, screen, tool, attachment, current.

- Never removed: current request, constraints, corrections, approvals, system, and segments
  marked protected (verification-critical tool results, recipient identity, required format).
  If those alone exceed the budget the pack reports `over_budget` rather than cutting them.
- Everything else: relevance (term overlap with stemming, recency, a boost for the last turn
  when the request refers back — "it", "that", "explain it again"), exact and near-duplicate
  removal, tool/screen output compression, per-source budget shares.
- Dropped turns become an extractive summary, each line tagged `[user said]`, `[observed]`,
  `[model inferred]` or `[unverified]`.
- Memory retrieval only when the request is about the person or the past.
- Token counts: tiktoken cl100k for every model, 10% margin.
- Stable parts go first so provider prefix caches (Groq/Gemini automatic caching) can hit.
- Debug: `GET /brain/context` and Brain → Usage show categories and token counts, never text.

Measured on the synthetic Case I conversation (pinned constraint, 40 irrelevant turns,
correction, pending approval, needed tool result, noisy page, recent request):
**1 862 → 222 tokens (−88 %)**, all five protected items kept, the irrelevant turns gone.

## Caching

`cache.ScopedCache`: per (user, session) unless declared shared; every entry expires; LRU-bounded.
Refused by kind (screenshot, approval, message_body, password, otp, token, email_body) and by
content (anything secret-looking). Research: 15 min for "today/latest/price/score/weather",
6 h otherwise, always with retrieval time.

## Study (Class 10 / NCERT)

`study.detect`: subject, chapter (rationalised Class 10 syllabus table), class, marks (1–5, digits
or words, Hindi numbers), mode (explain / formal / revise / quiz / check / quote), style
(exam, steps, simple, final only), formula-first, language. Explicit markers (NCERT, CBSE,
"3 marks", quiz me) or an explanation verb plus a syllabus topic; "why does metal feel colder
than wood?" stays a normal question.

Labels are honest: **NCERT-style answer (N marks)**, **Based on the supplied chapter**, or
**General Class 10 explanation**. The prompt forbids claiming an NCERT quote; an exact
quote/page request without supplied text is answered "I can't … without the textbook" with no
model call. Quiz keeps one question per turn and checks the next reply against it.

## Research

Freshness-sensitive requests ("latest", "today", prices, scores, weather, "is X still…", years)
search DuckDuckGo's HTML endpoint (no authenticated browser, stops on a bot check), pass at most
4 × 300-character excerpts as numbered untrusted references, and end with sources and retrieval
time. No sources → "I won't answer that from memory". Electricity's "current" is not freshness.

## Tool-calling reliability

`toolcheck.guard` (on top of `tool_contract`): native tool-call field only, no invented tools,
argument repair limited to mechanical fixes, no side-effect tool on a turn that asked for no
action, recipients must come from the owner's words — not from a webpage, email, OCR, message or
tool output. Probes: correct selection, required fields, no tool for chit-chat, no invented tool,
recipient preserved, injected tool output ignored (6/6 required); vision: one generated red
image, one word. Brain → Providers → **Validate** runs them (a few tiny requests, nothing executed).

Until some model passes, `tool_choice` returns None and the tool loop keeps the configured model;
the Brain tab says "actions use the configured model unverified".

## Local model strategy (measured here, RTX 3050 4 GB, 23 GB RAM)

| Model | Size | Local role checks (7) | Warm first token | Cold start | Tool probes | Use |
|---|---|---|---|---|---|---|
| qwen2.5:3b | 1.9 GB | 7/7 | ~0.38 s | 46 s | 5/6 (calls a tool on "thanks") | offline/private chat, intent, JSON, short Hindi/Hinglish — not tools |
| qwen3.5:4b | 3.4 GB | 0/7 (thinking model: empty content within 120 tokens, ~14 s/call) | ~14.6 s | 69 s | 3/6 | none — denied in `registry.DENIED`; re-measured 2026-09-28: answers only with Ollama's `think:false`, 8–15 s on CPU |
| moondream | 1.7 GB | — | — | — | passed earlier; 2026-09-28 on CPU: empty answer (0/1) | not verified here — no local vision |
| nomic-embed-text | 0.3 GB | — | — | — | — | memory embeddings |

The smallest model that passes each role is recommended (`local_models.recommend`): qwen2.5:3b
for all seven. The 46 s cold start means offline answers are slow unless Ollama keeps it loaded
(`OLLAMA_KEEP_ALIVE`, see `.env.example`). Nothing is downloaded by the brain.

## Offline behaviour

Deterministic commands never touch a model. With no internet: chat goes to the local model with
the offline sentence; study/research/hard questions say they need a cloud model; one network
failure is enough to skip the rest of the cloud candidates for 30 s.

## Setup and the Brain tab

`GET /brain/setup` reports whether any route can answer, which providers have keys (never the
keys), local resources, a recommendation and what stays unavailable. The Overview shows a setup
card when chat has no usable model. Views: Overview, Providers, Routing, Local, Usage.

API (all under `/brain`): `overview`, `providers` (GET/POST), `providers/{id}` (PATCH),
`providers/{id}/test|refresh|models`, `providers/{id}/keys` (+ `order`, `migrate`,
`{kid}` PATCH, `{kid}/test`, `{kid}/delete-request`, `{kid}` DELETE?token=), `models/validate`,
`models/replace`, `routing` (GET/PUT), `usage`, `context`, `setup`, `setup/complete`,
`local`, `local/{model}/load|unload|benchmark|delete-request`, `local/{model}` DELETE?token=.

## Observability

`$JARVIS_STATE_DIR/brain-events.jsonl`, allow-listed fields only (request id, source, intent,
route, tier, capabilities, language, privacy level, provider/model, fallback reasons, capability
lost, tokens, context before/after, latency, cost estimate, status, loaded commit). No prompt,
reply, message, subject, file content, screenshot, key or full phone number. Rotated at 1 MB,
two old files kept.

## Troubleshooting

- *"No model has passed the tool-calling check"* — Brain → Providers → Validate on a cloud model.
- *A provider shows paused:permission_denied* — the key/project is refused (Gemini's key had
  this on 2026-09-23); fix it in the provider console, then Test.
- *A model shows "removed"* — the provider no longer lists it; choose the suggested replacement.
- *Offline answers are slow* — the local model is being loaded (46 s cold); keep it resident.
- *Everything should go back to how it was* — `JARVIS_DAILY_BRAIN=0` and restart.
- Keys stored in the file backend — the Secret Service did not answer at startup; check
  gnome-keyring is running.

## Integration notes for feat/jarvis-3d-studio

- Declare new needs as capabilities, not providers: add a string (e.g. `image_to_3d`) to a model's
  declared set in `registry.KNOWN` or via a user model entry, and ask for it in a `BrainRequest`.
  If it must be proven, add it to `Cap.MUST_VERIFY` with a probe in `toolcheck`.
- For vision on an image: `brain.daily.brain().respond(text, images=[data_uri])` routes only to a
  verified vision model and returns the honest notice when none exists.
- For a one-off completion: `jarvis.llm.complete(system, prompt, strength="strong")` — unchanged
  signature, now routed.
- Likely overlap: `overlay/index.html` and `overlay/app.js` (tab list, Alt+N shortcut, palette),
  `jarvis/webserver.py` (router includes), `tests/conftest.py`, `README.md`, `HANDOFF.md`.

### Integration order (done on `feat/jarvis-integrated-v1` — see docs/INTEGRATION.md)

1. Finish and verify `feat/jarvis-daily-brain` and `feat/jarvis-3d-studio` independently.
2. Merge `feat/jarvis-daily-brain` into a fresh integration branch from `live-failure-repair`.
3. Merge or rebase `feat/jarvis-3d-studio` onto that; resolve the overlay tab list / Alt+N
   shortcut (Brain is Alt+5), `webserver.py` router includes, `tests/conftest.py` fixtures.
4. Run the full suite and live smoke tests once; restart services only from that checkout.
5. After restart: Brain → Providers → Validate on the cloud models (tool + vision probes), and
   optionally "Move to keyring" for the `.env` keys. No migration runs automatically; the first
   start creates `brain.json` only when a setting is saved.
