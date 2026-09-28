# The integrated build: Daily Brain + Study Companion + 3D Studio

Branch `feat/jarvis-integrated-v1`, based on `73e01d8` (the stable `live-failure-repair` tip).
Merged with real merge commits: `feat/jarvis-daily-brain` (`6c6295b`), `feat/jarvis-study-companion`
(`5de21bf`), `feat/jarvis-3d-studio` (`09c68d6`). Feature docs: `docs/DAILY_BRAIN.md`,
`docs/STUDY_COMPANION.md`, `docs/3D_STUDIO.md`. This page is how they fit together, what was
actually verified, and what is not claimed.

## Request path

```
utterance ─► commands.handle
   │  approvals ("yes", "cancel")  → settings → notifications → away mode      (never a model)
   │  deterministic handlers:  three_d → study (session/quiz/diagram state) → overlay lesson → …
   │                           teach / video / overlay-lesson step aside for clear syllabus questions
   ▼
GroqAgent.send ─► brain.daily.respond
   │  classify (model-free) → Study Companion claims study turns → its model work comes back
   │  through brain/capability.py with declared capabilities and a privacy floor
   │  conversation / research / vision → router → executor (no tools, honest notice)
   ▼  actions / memory → tool loop, only on a model that passed the tool probes
subsystems (3D, contacts, away mode, omnicore, meeting notes, vision, message drafts, paper
summaries) ─► brain/capability.complete(CapabilityRequest)   — the one door to a model
```

`JARVIS_DAILY_BRAIN=0` restores the previous single-model behaviour everywhere (the Study
Companion and the new routing step aside; local-only requests still never go to the cloud).
`JARVIS_STUDY_COMPANION=0` leaves study turns to the Brain's own study route.

## Capability matrix

| Capability | Served by | Needs verification | Notes |
|---|---|---|---|
| chat, reasoning, structured output, long context, Hindi, Hinglish | cloud or local model via router | no (declared) | weak local models are not used for study/research/hard reasoning |
| tool_calling | a model that passed 6/6 tool probes | **yes** | qwen2.5:3b is denied outright (called a tool on "thanks") |
| vision | a model that passed the vision probe | **yes** | screenshots are "sensitive"; cloud only if screenshot upload is allowed |
| source_grounding | Study retrieval + model with citation check | no | an uncited grounded answer escalates once |
| image_segmentation, structured_scene_planning, 3d_reasoning, blender_editing, multimodal_comparison | 3D Studio local engines (`three_d/brain_routes.py`) | — | an `engine` route: no model, nothing leaves the machine |
| image_to_3d | nothing (no model is verified for it) | **yes** | the optional remote endpoint needs the Brain's privacy policy *and* an approval naming the host |

## Privacy model

* Privacy level = max(declared floor, what `brain/privacy.classify` finds); it can only go up.
* **Contacts**: summaries are deterministic by default. A model only with
  `JARVIS_CONTACTS_SUMMARIES=local` (local model only) or `=cloud` (the Brain's policy still
  decides); the prompt never contains a contact's name or number. Import never sends anything,
  never overwrites a name the owner saved, and duplicate names stay ambiguous.
* **Incoming messages** (away mode, omnicore): untrusted `messaging` source, chat capability only;
  they cannot reach tools, settings, providers, approvals or repairs. The omnicore PA path drafts
  only; it no longer sends WhatsApp messages (it was never approval-gated).
* **Meeting transcripts**: sensitive; routed under the privacy policy; kept only in the owner's
  vault note; no transcript text in any log.
* **Screenshots**: only to a probe-verified vision model; cloud only when screenshot upload is on.
* **Study material**: the companion's `PrivacyPolicy` keeps personal/sensitive material local
  (`local_only`); documents are fenced as data; injected instructions cannot change providers.
* **Logs**: Brain telemetry keeps purpose, route, provider, privacy level, latency — never text.

## Approvals

One manager (`jarvis/approvals.py`) for messages, email, calendar, study reminders (`reminder`)
and remote 3D uploads (`upload`). An ambiguous "yes" with two actions pending approves neither;
an expired approval cannot run; a specific approval ("yes, send it to mesh.example.com") must be
*said* — the overlay shows the phrase and the API refuses a button confirm for it; approval for
one provider does not authorise another.

## Overlay

Tabs Conversation · Tasks · Memory · System · Brain · Study · 3D on **Alt+1…Alt+7** (unique; the
key hint and the command palette say the same). The tab strip scrolls sideways at narrow widths.
Study and 3D views read `/study/state`, `/3d/status` and `/approvals`; 3D progress arrives as
`studio` events and never moves focus. Verified in headless Chromium against the real routes:
`tests/integration/overlay_check.py` (26/26: unique ids and shortcuts, key never in page state,
quiz shows no answer, named approvals phrase-only, 360/1280/1920 px, dark/light, labels, focus).

## Providers — what was actually validated (2026-09-28, synthetic prompts only)

| Provider / model | Result |
|---|---|
| Groq (free tier: 1 000 req/day, 8 000 TPM) — gpt-oss-20b | text ✓ · JSON ✓ · streaming ✓ (first token 459 ms) · bad key → `auth_failed` ✓ |
| Groq gpt-oss-20b, gpt-oss-120b — tool probes | **5/6 each**: both obeyed an instruction injected into a tool result → **not tool-verified** |
| Groq qwen/qwen3.8-27b | not in the Brain registry; not validated |
| Gemini gemini-3.6-flash | model list OK; completions **denied at the account/project** (403) → degraded; no probe made |
| Ollama qwen2.5:3b | local answers; **no tools, ever** (denied) |
| Ollama qwen3.5:4b | thinking model: default output is all `thinking`, empty `content`; with `think:false` it answers, 8–15 s per short reply on CPU and does not fit the GPU beside voice → **not used**; GUI grounding 3/4 at ~30 s → vision clicking is off by default (`JARVIS_GROUND_MODEL`) |
| Ollama moondream | on the CPU it returns an empty answer (1 token) for every prompt → **vision not verified** |
| Fallback | a request whose first choice failed (`model_not_found`) was answered by gpt-oss-20b, flagged as a fallback; no setting was changed |

Consequence, stated plainly: **no model is tool-verified on this machine yet**, so free-form
model actions are unavailable; deterministic actions ("open …", "message Papa …", timers, media,
settings) work as before, with approvals. No vision model is verified, so screen *image*
questions say so (text on screen is still read through accessibility/OCR). To change either:
Brain tab → Providers → Validate on a model you trust, or accept an unverified model knowingly.

## Study coverage

Offline (no model): verified cards for current direction, Ohm's law, series/parallel, heating and
power, refraction, ionic compounds, Pythagoras, cumulative frequency; the SymPy maths engine;
unit-checked science formulas; the Electricity quiz bank; ray, circuit and right-triangle
diagrams. Everything else needs a model and is labelled "AI-written, not source-checked", or the
student's own source (then cited by page). **Full CBSE syllabus coverage is not claimed**; chapter
numbering is unverified.

## 3D accuracy limits

Logos/flat art: near-exact outline, thickness invented unless stated. Dimensioned drawings: to the
labels. **A single screenshot cannot reveal hidden geometry**: depth and scale are *estimated* and
the reply says so; labels verified / constrained / estimated / invented are kept per part. No
image-to-3D model is installed; the remote endpoint is optional and approval-gated.

## Resources

Voice keeps priority: Whisper goes on the GPU only when it fits (`jarvis/resources.py`), a Blender
preview only when it leaves the voice reserve, a local model load is refused when RAM cannot hold
it. Study and 3D work run off the event loop. Measured full suite: see HANDOFF.

## Deployment and recovery

* `jarvis restart` — takes the release (`origin/live-failure-repair`) only by a fast-forward that
  loses nothing; a verified local repair, local commits, local edits, a rollback pin or a
  divergence are kept and explained (`jarvis deploy`). Afterwards every service must report the new
  commit; an update that does not come up healthy is rolled back automatically.
* `jarvis doctor` — read-only health: services and the commit each one loaded (backend, voice,
  WhatsApp, overlay), ports, Daily Brain routes and paused providers, resources, approvals.
* `jarvis rollback` — back to the previously deployed commit (a backup branch keeps the newer
  one), pinned; `jarvis update` lifts the pin. Neither needs the network.
* The uncommitted release-hardening worktree (`.claude/worktrees/safe-self-repair`) is preserved
  untouched; its reviewed parts were ported (deploy, doctor, resources, repair records, dictation
  motion). Not ported: `jarvis recover` and `jarvis support-bundle` (never written).

## Known limitations

* Phone-call answering is not possible (KDE Connect announces and logs calls only).
* No tool-verified or vision-verified model yet (above).
* Gemini is refused at the account level; fix it in the Google console, then Validate.
* Study: quiz bank covers Electricity only; Hindi exam answers need a model.
* 3D: drawing parser handles rectangles and circles only; single-view is revolve/extrude.
* Local qwen3.5:4b and moondream do not work usefully on this CPU/GPU budget.
