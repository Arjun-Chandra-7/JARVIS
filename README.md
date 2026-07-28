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
.venv/bin/python -m jarvis --web         # backend + WebGL HUD at http://127.0.0.1:8770
```

### HUD (Iron-Man console)

The WebGL HUD (`webui/`, served by `--web`) is the visual face of Jarvis: an energy-core
that reacts to state (standby / listening / thinking / speaking), **live system telemetry**
(CPU, memory, GPU, disk, battery — real, polled from `/stats`), a **live subsystem health**
panel (brain, memory, voice, Google, phone, WhatsApp — from `/health`), one-tap **quick-action
chips**, browser voice input, and a conversation log that **persists across reloads**.

```bash
bash scripts/hud.sh          # one command: backend + open the HUD in an app window
bash scripts/hud.sh voice    # …and also start the wake-word voice loop
```

Shortcuts inside the HUD: `/` focus input · `Esc` blur · `↵` send · click the core to pulse.

Backend API (all CORS-open, used by the HUD and the Electron overlay):
`POST /chat` · `GET /stats` · `GET /health` · `GET /history` · `DELETE /history` ·
`GET /suggestions` · `GET /weather` · `GET /nearby` · `GET /whatsapp/inbox` ·
`GET /events` (SSE) · `POST /emit` (voice/phone/presence → HUD).

### Overlay situational awareness (all real, no fake decoration)

The Electron overlay's ambient layer shows only **live** data:
- **Proximity radar** — real nearby devices plotted by signal: WiFi APs (`nmcli`), LAN
  devices (`ip neigh`), and Bluetooth devices (`bluetoothctl`, phones flagged). Backed by
  `jarvis/nearby.py` (bounded background scans, cached) → `GET /nearby`.
- **People nearby** — webcam face detection (MediaPipe) counts people in front of you, shows a
  badge on the optics and plots them on the radar (`overlay/presence.js` → `/emit` → ambient).
- **WhatsApp feed** — recent messages from the live bridge (`GET /whatsapp/inbox`).
- **Subsystems** — green/red status for brain, memory, voice, Google, phone, WhatsApp.
- **Diagnostics** — real CPU / MEM / GPU / disk. **Weather** for your city.

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
