# Jarvis

Jarvis is a local, voice-first personal assistant for a Linux desktop. It combines an optional
ChatGPT-web, Gemini, or Groq reasoning backend with local desktop integrations and an Obsidian-style
memory vault.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt  # tests
cp .env.example .env
```

The default brain is `chatgpt`: sign in once with `python -m jarvis --chatgpt-login`. Gemini and
Groq require their respective API keys. Voice, Google, Telegram, WhatsApp, and phone features each
need their own account, device, or desktop service configured before use.

## What it does

- Text chat, wake-word voice, and an Electron overlay HUD (`overlay/`, the primary interface).
  A browser HUD at `/` remains as a fallback for machines without Electron.
- Memory in one SQLite store: vault notes, past conversation, and distilled facts, searched by
  BM25 and embeddings together and retrieved automatically on every turn.
- Screen reading, browser and desktop control, system/media controls, timers, reminders, routines,
  research, and coding-task delegation.
- Google Calendar, Gmail, and Tasks after OAuth; Telegram, WhatsApp, Instagram, KDE Connect, and
  n8n integrations when linked.
- Screen reading through a local vision model: it describes the screen and the language model
  answers from that description, because a 1.9B captioner answers "describe this" far better than
  "what app is open?".
- Desktop context (`what_am_i_doing`): focused window, open apps, whether you are away, whether a
  call is holding the screen awake. Unprompted announcements are held while you are in a call.
- Meeting note capture joins silently with microphone and camera disabled; admission is verified
  before recording.
- Away mode records direct incoming messages and replies only to direct incoming WhatsApp messages.
  Group, newsletter, status, outgoing, and duplicate messages are ignored. The away responder uses
  an isolated temporary ChatGPT tab with no tool access; it falls back to a neutral acknowledgement
  if unavailable.

- Human radar: webcam face detection gives bearing + metric range, acoustic FMCW gives
  range only, and paired devices give names. See `docs/HUMAN_RADAR.md`.

## Commands

```bash
.venv/bin/python -m jarvis --text
.venv/bin/python -m jarvis --voice
.venv/bin/python -m jarvis --web
.venv/bin/python -m jarvis --check
.venv/bin/python -m jarvis --selftest
.venv/bin/python -m jarvis --brief
.venv/bin/python -m jarvis --daemon
.venv/bin/python -m jarvis --meeting
.venv/bin/python -m jarvis --google-auth
.venv/bin/python -m jarvis --whatsapp
.venv/bin/python -m jarvis --telegram
.venv/bin/python -m jarvis --index          # rebuild the memory index over the vault
.venv/bin/python -m jarvis --consolidate    # distil recent conversation into durable facts
```

`bash scripts/overlay.sh` starts the overlay HUD, which also supervises the backend and the
always-listening voice loop.

## Checking it works

```bash
.venv/bin/python -m jarvis --check          # dependencies, keys, audio devices, speech detection
.venv/bin/python scripts/audit.py           # exercise every subsystem and report what works
node scripts/overlay-smoke.js               # (under xvfb-run) load the real overlay and report
.venv/bin/python scripts/hud-shot.py o.png  # screenshot the HUD over a representative desktop
```

`audit.py` calls the real endpoints and dispatches the real read-only tools. Tools with side
effects are reported as explicitly skipped rather than passed, so it never overstates coverage.

`bash scripts/hud.sh` starts the local HUD. `bash scripts/hud.sh voice` also starts the wake-word
loop. The web server binds to `127.0.0.1` by default.

## Privacy and safety

Jarvis asks before destructive or outbound actions where its integration supports confirmation.
Imported WhatsApp history and away-mode records live under `Jarvis/private/`; they are excluded
from vault git commits, ordinary recall, and the semantic index. Importing chat context is read-only:
it does not send messages, delete chats, or mark messages as read. Only an explicitly authorized
local-user query should retrieve that context.

Run the suite with:

```bash
.venv/bin/python -m pytest -q
```
