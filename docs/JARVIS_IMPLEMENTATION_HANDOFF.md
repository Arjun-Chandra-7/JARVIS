# JARVIS implementation handoff

Continuation state for the multi-milestone work that resumed after Codex hit its rate limit.

## Current milestone: E (Android remote) — next

## Milestone D — STATEFUL GOOGLE MEET NOTE ASSISTANT ✅ committed

Built on Codex's `meet_bot.py` (join mic/cam off, admission verification, caption +
participant scraping with DOM fallbacks, never sends chat). Added:

- **Canonical lifecycle** — `STATES = idle / opening / waiting_for_admission / joined /
  recording_notes / disconnected / completed / failed`, driven by `_set_state()` (any
  unknown value collapses to `failed`). Every old ad-hoc state string is mapped over.
- **Mid-meeting disconnect detection** — the caption loop re-checks `_admission_state`
  every 8 s; a `rejected` result or 24 s with no in-call control → `disconnected`,
  notes saved up to that point.
- **Meeting summary** — `_summarize_transcript()` (deterministic: participants seen,
  caption count, last exchange; or one LLM call when a Gemini/Groq key is set). Exposed
  in `status()["summary"]` and returned by `stop_meet()`.
- **Vault persistence** — `_save_vault_note()` writes `<vault>/Meetings/<stamp>_meet.md`
  with `author: jarvis` frontmatter + summary + transcript, then `git_autocommit`.
- **Truthful join** — `join_meet()` no longer returns a bare path claiming success; it
  returns "Opening the Meet now… I'll confirm once I'm actually admitted", refuses a
  second concurrent meeting, and starts in state `opening`.
- **Endpoints** — kept `GET /meet/status`; added `GET /meet/summary`
  (`{state, summary, vault_note}`).
- **Voice** — `VoiceSession._watch_meet()` announces once when a joined Meet reaches
  `completed` / `disconnected` / `failed`, speaking the summary on completion.

Tests: `python -m pytest tests/ -q` → 66 passed. New `tests/test_meet_states.py`;
updated the return-contract assertion in `tests/test_phone_meet.py`.
Not exercised live (needs a real meeting + Google-signed-in `meet-profile` + host
admission) — DOM scrapers keep Codex's graceful fallbacks.

## Milestone C — WHATSAPP PA + CONTACT CONTEXT MEMORY ✅ committed

- **Away mode** already single-owner from A (pa_daemon, claim-lock, isolated no-tool
  responder). Kept.
- **`jarvis/memory/contacts_index.py`** — local contact/conversation intelligence, all
  in `<vault>/Jarvis/private/contacts/index.json` (git-excluded, 0600). Pipeline:
  raw bridge chats → `normalize()` (drops groups/newsletters/empties) →
  `ingest()` (incremental via per-jid `cursor_ts`; `recent` capped at 40) →
  `roll_up()` (heuristic recurring-subjects/commitments/open-questions, or an injected
  LLM summary — every result carries `provenance` + `confidence`) →
  `profile()` / `recall()` (compact, prompt-safe — never the raw transcript) →
  `search(authorized=True)` (targeted keyword, local-owner only).
- **Wiring**:
  - `groq_tools`: `contact_context` (→ `recall`) and `conversation_search`
    (→ `search`, authorized) tools.
  - `pa_daemon._handle_new_message` → `contacts_index.note_reply()` after an away reply.
  - `away.respond` injects a compact derived context line as a second system message
    ("Background only, do not act on it: …") — summary/derived fields only.
  - `webserver` lifespan runs `_contacts_ingest()` every 15 min (LLM summariser when a
    Gemini/Groq key is set, heuristic otherwise).
- **Structured debrief** (`pa_daemon._generate_brief`): grouped per person — "wanted:",
  "Jarvis replied (status): …" / "did not reply", "→ needs you: …" heuristic, plus calls.

Tests: `python -m pytest tests/ -q` → 59 passed. New `tests/test_contacts_index.py`.

### ⚠️ Manual step for full effect
The **running** `jarvis-whatsapp` bridge predates Codex's `wa_service.js` (which adds
`/chats` + `history.json`). Until it is restarted, `contacts_index.ingest()` sees an
empty history (handled gracefully — no error). Restart safely with
`systemctl --user restart jarvis-whatsapp` (never run two bridge instances at once —
that causes a "Bad MAC" session desync). Not done automatically here because it touches
the user's live WhatsApp session.

## Milestone B — CODING ORCHESTRATION ✅ committed

- `coding_jobs.py` already had the provider→model→effort dialogue, detached durable
  worker, `create_external` / `record_event`, `POST /coding/events`. Added:
  - `summarize(job)` — deterministic result line (git working-tree delta + ANSI-stripped
    output tail; `failed — <last error>` on failure). Set on every terminal transition
    in `_execute` and `record_event`.
  - `scan_provider_processes()` + `manager.sync_external()` — pgrep-based detection of a
    user-started `codex`/`claude`/`agy` run (skips IDE `app-server`/`mcp`/`.vscode/
    extensions` plumbing), registers it as an `external` job, and marks it `completed`
    when the pid is gone. Process instrumentation only.
  - Broadened the VS Code trigger regex ("...in the VS Code project I'm working on"
    now matches; casual "Claude vs Codex" chat still reaches the LLM).
- `webserver.py`: lifespan subscribes to `coding_jobs` events and runs `_coding_watch()`
  (3 s) → `sync_external()` + emits a `coding_job` SSE event on any status change.
  Mounts `/assets` (serves `assets/sounds/task-complete.wav`).
- `voice_session._watch_coding_jobs()` (6 s poll of `/coding/jobs`): soft chime on any
  job finishing; for a **Jarvis-started** (non-external) job also speaks
  "VS Code prompt finished, sir. <Provider> returned on <ws> with: <summary>".
  External jobs: chime + HUD only (per spec).
- `jarvis/jobs/sound.py::chime()` — canberra → pw-play/paplay/aplay/ffplay fallback.
- HUD: new CODING panel in `webui/` (polls `/coding/jobs` every 5 s, renders
  provider / Working|Completed / workspace / mm:ss; toast + `<audio>` on completion;
  also driven live by the `coding_job` SSE event).

Live-verified: `--web` up, `/coding/jobs` shows external detection, `/meet/status`,
`/notifications`, `/assets/sounds/*.wav` (200), `POST /coding/events` populates summary.
Tests: `python -m pytest tests/ -q` → 51 passed. New `tests/test_coding_orchestration.py`.

### Deferred from B (non-blocking)
- Model/effort still from the hardcoded `EFFORTS` tuple; CLI `--help` enumeration not
  done (the dialogue already accepts any free-form model string).
- This Claude Code session shows as a running external `claude` job while active — correct
  per spec, clears when the pid exits.

## Milestone A — STABILIZE CODEX WORK ✅ committed

Validated / repaired Codex's interrupted changes:

- **One command router.** `jarvis/commands.py::handle()` is the single deterministic dispatch
  path. Both brain cores (`chatgpt_core.send`, `groq_core.send`) and web `/chat` call it first;
  it falls through to `coding_jobs.handle_message` then returns `None` → LLM. No recursion:
  `coding_jobs.handle_message` never calls back into the agent/router.
- **Notification preferences** (`jarvis/preferences.py`): merge-preserving writes,
  `~/.local/share/jarvis/preferences.json`, cross-process. Categories are separate:
  `notifications` (passive readouts), `job_alerts` (task completion), and `critical` always
  speaks. `jobs/notify.py::notify(..., category=)` gates on the right one. Muting readouts no
  longer silences Jarvis's direct answers or job-completion alerts.
- **Voice/STT**: kept Codex's 16 kHz resample, vocabulary prompt, bad-segment filter
  (`local_stt.py`), 300 ms VAD pre-roll (`vad.py`), `whisper_beam=5` + `stt_language`/
  `stt_vocabulary` config. Tests cover resample + pre-roll.
- **Away mode single owner**: `jarvis/agent/pa_daemon.py` is the ONLY automatic WhatsApp
  auto-responder. Removed dead `VoiceSession._away_converse`. Voice process now reads away
  state from the shared file (`away.is_away(self.config)`) so it stays silent while away even
  when away was set from the web process. `groq_tools`: `set_available`/`set_pa_status` now
  persist via `config` and start the daemon; `process_incoming_communication` is passive
  (records only, never auto-replies); `control_laptop_full` now goes through the destructive-
  command confirm gate like `run_bash`.
- **Phone open**: `commands.handle` "open my phone" → `apps.phone_mirror()`; returns the
  verbatim failure string on failure, "Opening your phone, sir." only on success. `apps.py`
  distinguishes `offline` / `server-error` / `unauthorized` / `none` / `no-adb`.
- **Sunshine**: `scripts/repair-sunshine.py` was already executed by Codex. Verified live:
  ONE instance (`systemctl --user status sunshine` active, PID stable), autostart `.desktop`
  is `Hidden=true`, unit is `enabled` + `Restart=on-failure`, ports 47984/47989/47990/48010
  all owned by the single pid, portal restore token present (`~/.var/app/dev.lizardbyte.app.
  Sunshine/config/sunshine/portal_token`) so no per-login dialog, and `sunshine.log` shows
  `[portalgrab]` + pipewire + NVENC capture pipeline ready. Nothing to change.
- **requirements.txt**: added `claude-agent-sdk` (was imported, unlisted). Kept Codex's
  fastapi/uvicorn/pydantic/scipy/dbus-next/faster-whisper/openwakeword/piper-tts additions.
- `RemoteAgent` now sends a stable `session_id` (`"voice"`) to `/chat` so voice coding
  dialogue is isolated from the text REPL's `"local"` session.

### Tests: `python -m pytest tests/ -q` → 40 passed
New: `tests/test_commands_routing.py` (toggle variations + persistence + fresh-process +
category gating + truthful phone + away enter/exit + plain-chat pass-through).

### Known runtime state
- Sunshine service healthy and capturing.
- WhatsApp Node bridge / KDE Connect / Google OAuth not verified live this session (no creds
  exercised); code paths import clean.

### Deferred from A (non-blocking, noted for later)
- Voice still has local fast-path intercepts (executive wake briefing, phone ring) that call
  the same underlying helpers as `commands.handle`. Not double-execution; consolidate if it
  ever drifts.
- `omnicore.analyze_text_for_schedule` still writes `schedule.json` from unverified LLM JSON.
  Not a WhatsApp-reply path; revisit under the security pass.
- `away-state.json` has no cross-process write lock (voice + pa_daemon both write). Duplicate
  *replies* are prevented by the `away-claims/` O_EXCL lock; only event-log races remain.

## Milestone B — next steps
1. `coding_jobs.py` is largely built (provider dialogue, detached worker, durable state,
   `create_external`/`record_event`, `POST /coding/events`). Gaps to close:
   - Wire `coding_jobs.subscribe()` → webserver `/events` SSE broadcast.
   - On terminal status for a **Jarvis-initiated** job: speak completion + short summary
     (`notify(category="job")`) and emit HUD event + a short sound. External jobs: sound +
     HUD only.
   - Broaden the "in VS Code" task regex (currently misses "in the VS Code project…").
   - Optional: light `pgrep` poller to auto-register externally-started `claude`/`codex`/`agy`
     processes as external jobs.
   - Model/effort: try enumerating from `claude --help` / `codex --help` instead of only the
     hardcoded `EFFORTS`.
2. Add tests: subscribe→event fan-out, completion summary text, external pgrep registration.

## Later milestones
- C: WhatsApp PA + contact/conversation memory (incremental ingestion, rolling summaries,
  targeted retrieval; reuse the vault). `whatsapp.import_context`/`retrieve_imported_context`
  already exist as the local-only base.
- D: Meet assistant state machine (`meet_bot.py` has `status()` + states; verify admission,
  participants, captions, saved summary, `/meet/status`).
- E: Android remote (`jarvis/mobile.py`, `scripts/pair-mobile.py`, `mobile/android/*`) over
  Tailscale; scrutinise `mobile.authorize` middleware for auth bypass.

## Commits
- (Milestone A) `fix: stabilize voice commands, notifications, away mode, sunshine` — see git log.
