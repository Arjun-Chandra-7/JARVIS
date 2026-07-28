# Jarvis — build handoff (built while you were AFK)

## TL;DR
Lots of progress. Jarvis is a working voice-first assistant that runs on **your Claude subscription
(no API key)** via the Claude Agent SDK, with an **Obsidian vault as its memory brain**. I built the
brain, memory, background-task delegation, semantic recall, screen vision, voice, and proactive
routines — testing everything I could on your subscription.

**Two blockers hit near the end**, both from the same cause (Opus temporarily throttled):
1. Your subscription's **session/usage limit** — a background task came back with
   *"You've hit your session limit · resets 2:40am (Asia/Kolkata)"*.
2. This dev environment's **own Bash safety classifier** (which uses Opus) went unavailable, so I
   couldn't run final import/unit checks.

So the last-built pieces (proactive routines + a small autonomous-helpers refactor) are **written but
not yet run**. Everything before that was verified live. Test commands are below for when the limit
resets.

---

## What's verified working (ran live on your subscription)
- **Text brain** — `claude-opus-4-8` via Claude Code, agentic tool use, in-character replies.
- **Computer control** — built-in Bash tool behind a safety gate (confirms destructive commands).
- **Obsidian memory** — reads/writes the vault, **git auto-commits every turn**, pinned profile;
  it saved "Arjun, Bangalore, building Jarvis" to `profile.md` and committed it.
- **Permission gate** — confirms destructive shell + edits to notes *you* wrote; unit-tested.
- **`recall` tool** — hybrid semantic+keyword vault search (keyword verified live).
- **`dispatch_background_task`** — a background agent ran a task to completion, wrote it to
  `Jarvis/tasks/…md`, git-committed, and fired a notification. **This is the headline feature.**
- **VAD / utterance capture** — unit-tested (captures speech, ignores silence, caps run-ons).
- **Custom SDK tools** work end-to-end on the subscription.

## Built, but needs your keys / hardware / Ollama to verify
- **Voice (Phase 5)** — wake word ("Jarvis") → Deepgram STT → brain → ElevenLabs TTS, with
  follow-ups + barge-in. Needs `libportaudio2` and 3 keys.
- **Semantic recall (Phase 2b)** — the *semantic* half needs Ollama; keyword half already works.
- **Screen vision (Phase 6a)** — `capture_screen` tool; needs a graphical session + a screenshot
  tool (`scrot`/`grim`/`gnome-screenshot`/…).
- **Proactive routines (Phase 4)** — morning brief + condition monitors + a scheduler daemon.
  Written but **not run** (blocked by the limit). Verify the import first (see checklist).

## Not built yet
- **Phase 6b — meeting/capture mode.** Reuses the STT pipeline; not built.

> **Update (2026-07-15):** Phase 3 — Google Calendar / Gmail / Tasks — is now **built**. Run
> `python -m jarvis --google-auth` to connect (needs a Google Cloud OAuth "Desktop app" client at
> `~/.config/jarvis/client_secret.json`). See README → *Google*. Reads are free; sending email /
> creating events are confirmed first. Offline-verified; not live-tested (needs your consent).

---

## Commands
```bash
.venv/bin/python -m jarvis --text            # terminal chat (works now, once quota resets)
.venv/bin/python -m jarvis --task "…"        # dispatch ONE background task, wait, print result
.venv/bin/python -m jarvis --brief           # run the morning briefing once, now
.venv/bin/python -m jarvis --daemon          # run proactive routines (scheduled brief + monitors)
.venv/bin/python -m jarvis --index           # build the semantic memory index (needs Ollama)
.venv/bin/python -m jarvis --check           # preflight: deps, voice keys, audio devices (no API)
.venv/bin/python -m jarvis --selftest        # one live TTS → mic → STT round trip
.venv/bin/python -m jarvis --voice           # wake word + speech
```

## Test checklist (after the limit resets, ~2:40am IST)
1. **Sanity import** (the one thing I couldn't run):
   `.venv/bin/python -c "import jarvis.__main__; print('ok')"`
2. **Brain + memory:** `--text`, then *"recall what you know about me"* and *"what's in my home dir?"*
   Then `git -C ~/JarvisVault log --oneline` to see it remember.
3. **Background task:** `--task "research the 3 best mechanical keyboards under 10k INR, briefly"` —
   watch it finish and write to `~/JarvisVault/Jarvis/tasks/`.
4. **Routine:** `--brief` (should speak/print a morning summary), then `--daemon`.
5. **Semantic memory:** install Ollama → `ollama pull nomic-embed-text` → `--index` → then `recall`
   in `--text` catches fuzzy matches.
6. **Voice:** `sudo apt install libportaudio2`; add `PICOVOICE_ACCESS_KEY`, `DEEPGRAM_API_KEY`,
   `ELEVENLABS_API_KEY` to `.env`; `--check` → `--selftest` → `--voice`.

---

## Known limits & notes
- **Subscription session limits are real.** Heavy background tasks + voice + routines can exhaust
  them (that's what we hit). Jarvis now detects the limit message and marks background jobs
  `rate_limited` instead of `done`. For always-on use you may want to throttle routine frequency.
- **Voice v1:** batch (not streaming) STT — a beat of latency after you stop talking. Barge-in may
  false-trigger from speaker echo (no AEC) — set `JARVIS_BARGE_IN=0` if so. Confirms are terminal.
- **Screen vision** works by saving a screenshot then Jarvis `Read`-ing it (Opus vision).

## Next steps (my suggestion)
1. Verify the checklist above once quota resets.
2. **Phase 3 (Google)** — biggest daily-value add; needs you to authorize Google.
3. Voice polish: streaming STT + spoken confirmations.
4. Meeting/capture mode (Phase 6b).

## File map (what's where)
```
jarvis/
  __main__.py            # CLI: --text/--voice/--daemon/--brief/--task/--index/--check/--selftest
  config.py              # all settings (.env-driven)
  agent/
    core.py              # JarvisAgent — the interactive ClaudeSDKClient loop
    prompt.py            # persona + abilities + memory guidance (pins profile/journal)
    permissions.py       # can_use_tool gate (destructive shell + human-note edits)
    autonomous.py        # background_gate, is_rate_limited, run_once (no-human runs)
    sdk_tools.py         # custom tools: recall, dispatch_background_task, check_*, capture_screen
  memory/
    vault.py             # vault structure, seeding, git autocommit, pinned reads
    embeddings.py        # Ollama nomic-embed (optional)
    index.py             # local vector index (chunk + cosine top-k)  [unit-tested]
    search.py            # hybrid recall (semantic + keyword, rg-or-python)
  jobs/
    runner.py            # background task runner → vault + notify
    notify.py            # console + notify-send
  audio/
    wake.py mic.py vad.py stt.py tts.py voice_session.py   # the voice pipeline
  vision/screenshot.py   # screen capture (multi-tool)
  routines/
    scheduler.py morning_brief.py monitors.py             # proactive routines  [not yet run]
```
Full phased plan: `~/.claude/plans/i-am-thinking-of-giggly-whisper.md`
