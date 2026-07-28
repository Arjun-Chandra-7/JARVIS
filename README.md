# Jarvis

A voice-first personal assistant with an Obsidian memory brain, in the spirit of Iron Man's JARVIS.
It drives Claude Code (via the Claude Agent SDK) **on your Claude subscription — no API key** —
remembers you across sessions via a git-versioned Obsidian vault, controls your computer, works
proactively, and can be reached by voice or from your phone.

See the phased build plan at `~/.claude/plans/i-am-thinking-of-giggly-whisper.md` and the current
state in `HANDOFF.md`.

## What it can do

- **Agentic brain** on `claude-opus-4-8` with Claude Code's built-in tools (Bash, Read/Write/Edit,
  Glob/Grep, WebSearch/WebFetch) → computer control, files, and web for free.
- **Obsidian memory brain** — a git-versioned vault (`~/JarvisVault`): durable profile + daily
  journal + per-project notes, pinned into context, auto-committed every turn.
- **Semantic recall** — hybrid keyword + local-embedding search over the vault (`recall` tool).
- **Background tasks** — "go research X" runs autonomously while you keep talking; result lands in
  the vault + a notification.
- **Screen vision** — `capture_screen` → Jarvis reads your screen (Opus vision).
- **Voice** — **local & keyless by default** ("Hey Jarvis" via openWakeWord → Whisper → Piper), or cloud (Deepgram + ElevenLabs) for top quality. Follow-ups + barge-in.
- **Proactive routines** — a scheduled morning briefing and condition monitors.
- **Remote access** — a Telegram bridge to talk to Jarvis from anywhere.
- **Safety gate** — confirms destructive shell commands and edits to notes you wrote.

## Setup

Requires the `claude` CLI (Claude Code) installed and logged in — that supplies the subscription
auth. No `ANTHROPIC_API_KEY` needed.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # optional — user name, model, vault path, voice/telegram keys
```

## Commands

```bash
.venv/bin/python -m jarvis --text        # terminal chat
.venv/bin/python -m jarvis --task "…"    # run one background task, wait, print result
.venv/bin/python -m jarvis --brief       # morning briefing, now
.venv/bin/python -m jarvis --daemon      # proactive routines (scheduled brief + monitors)
.venv/bin/python -m jarvis --telegram    # remote access via a Telegram bot
.venv/bin/python -m jarvis --index       # build the semantic memory index (needs Ollama)
.venv/bin/python -m jarvis --check       # preflight: deps, voice keys, audio devices
.venv/bin/python -m jarvis --selftest    # one live TTS → mic → STT round trip
.venv/bin/python -m jarvis --voice       # wake word + speech
```

### Voice
Two backends (auto-selected; force with `JARVIS_VOICE_BACKEND=local|cloud`):
- **local** *(default, keyless)* — openWakeWord **"Hey Jarvis"** + Whisper (STT) + Piper (TTS). No
  accounts. Needs `sudo apt install libportaudio2`; Whisper + a Piper voice download on first use.
- **cloud** *(best quality)* — Porcupine + Deepgram + ElevenLabs; set the 3 keys in `.env`.

Then: `--check` → `--selftest` → `--voice`. Say **"Hey Jarvis"** (local) / **"Jarvis"** (cloud), then talk.
If you hear nothing, set `JARVIS_OUTPUT_DEVICE` to your speaker's index from `--check`.

### Semantic memory (optional)
Install [Ollama](https://ollama.com), `ollama pull nomic-embed-text`, then `--index`. Without it,
recall falls back to keyword search.

### Remote (Telegram)
Create a bot with @BotFather, set `TELEGRAM_BOT_TOKEN` (and optionally `TELEGRAM_ALLOWED_CHAT_IDS`)
in `.env`, then `--telegram`. Remote turns deny destructive shell + edits to your notes for safety.

### Google (Calendar / Gmail / Tasks)
One-time setup:
1. [Google Cloud Console](https://console.cloud.google.com) → new project → enable the **Calendar**,
   **Gmail**, and **Tasks** APIs.
2. Create an **OAuth client ID** of type **Desktop app**; download the JSON.
3. Save it to `~/.config/jarvis/client_secret.json` (or set `GOOGLE_CLIENT_SECRET_FILE`).
4. `python -m jarvis --google-auth` → approve in the browser.

Then Jarvis reads your agenda/email/tasks and can send email / create events — reads are free,
sending email and creating events are confirmed with you first.

## Roadmap

| Phase | What | Status |
|---|---|---|
| 1 | Text-mode agentic brain | ✅ verified live |
| 2 | Obsidian memory (keyword) | ✅ verified live |
| 2b | Semantic recall (Ollama) | ✅ built (semantic needs Ollama) |
| 4 | Proactive routines | ✅ built (run `--brief`/`--daemon` to see it live) |
| 5 | Voice (local keyless *or* cloud) | ✅ built — local Whisper+Piper+openWakeWord verified; `--voice` |
| 6 | Background tasks · screen vision · Telegram | ✅ tasks verified live; vision/telegram built |
| 3 | Google calendar / email / tasks | ✅ built — run `--google-auth`, then it's live |
| 6b | Meeting / capture mode | ⏳ next |
```
