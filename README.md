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

- Text chat, wake-word voice, a browser HUD, and an Electron overlay.
- Durable profile/journal memory and optional local semantic recall through Ollama.
- Screen reading, browser and desktop control, system/media controls, timers, reminders, routines,
  research, and coding-task delegation.
- Google Calendar, Gmail, and Tasks after OAuth; Telegram, WhatsApp, Instagram, KDE Connect, and
  n8n integrations when linked.
- Meeting note capture joins silently with microphone and camera disabled; admission is verified
  before recording.
- Away mode records direct incoming messages and replies only to direct incoming WhatsApp messages.
  Group, newsletter, status, outgoing, and duplicate messages are ignored. The away responder uses
  an isolated temporary ChatGPT tab with no tool access; it falls back to a neutral acknowledgement
  if unavailable.

- Human radar: webcam face detection gives bearing + metric range, acoustic FMCW gives
  range only, and paired devices give names. See `docs/HUMAN_RADAR.md`.

## The overlay

One always-on-top window that changes shape rather than four competing ones:

| Form | What it is | How to get there |
| --- | --- | --- |
| **Pill** | Mic state and a dot when background work is running. Idles at ~1 % of one core. | default |
| **Conversation** | Live transcript, typed input, audio-reactive meter, stop/cancel. | click the orb, or the invocation shortcut |
| **Workspace** | Tabs for Tasks, Memory and System, plus a diagnostics inspector. | the expand button, or `Ctrl+K` |

`Ctrl+Super+Space` toggles it (editable under **System → Diagnostics**), `Ctrl+Super+J` opens the
workspace, `Ctrl+Super+H` hides it. `Esc` steps back: clear attached context, then collapse, then
hide. Size and position are remembered per form, and it opens on whichever display the pointer is
on. It hides itself automatically when a screen share starts.

Three things it can do that are worth knowing about:

- **Context lens** — "Explain selection" attaches whatever text you have highlighted (in any
  application) to your next message; "Read my screen" attaches a reading of the screen. You see
  exactly what was captured before it is sent, and it applies to that one request.
- **Tasks** — background jobs with their steps, the files they changed, and a cancel that reports
  honestly: queued work is dropped, a running process is only *asked* to stop.
- **Memory** — search what Jarvis knows, see which file and line it came from and when it was
  recorded, and correct or forget it. A correction keeps the old text in the note; "forget"
  removes the line from recall but says plainly that the vault's git history still has it.

## Computer control

Jarvis reads browser pages through Opera GX's live DOM when browser control is enabled. For native
apps it first reads the active window's AT-SPI accessibility tree, which exposes control labels and
screen bounds. If an app does not expose the target, Jarvis uses a screenshot plus the configured
vision model. It sends mouse and keyboard
input through `ydotool` on Wayland or `xdotool` on X11. It can click visible targets, drag, and hold a
button or key for a short interval. For a request such as "shoot towards the basket", Jarvis should
look at the game, choose a gesture, send it, and look again before reporting whether it worked.

Run `bash scripts/enable-control.sh` once if desktop input is unavailable. Screen targeting needs a
vision model (`qwen3.5:4b` in Ollama for target boxes, with `moondream` for general descriptions, or
a working Gemini key); it can decline a click when the target cannot be placed confidently. Games with
rapid timing, hidden state, or unsupported controllers can still
need game-specific controls.

## Voice tuning

Defaults are set from measurements taken on this laptop (see `docs/upgrade/RESEARCH.md`);
every one is reversible through the environment.

```bash
JARVIS_NEURAL_ENDPOINTING=0     # back to the old energy-threshold endpointing
JARVIS_ENDPOINT_HANGOVER_MS=300 # silence before Jarvis decides you have finished
JARVIS_WHISPER_MODEL=base.en    # small.en is more accurate on hard audio, ~3x slower
JARVIS_WHISPER_BEAM=1
JARVIS_LIVE_PARTIALS=0          # turn off the provisional transcript
JARVIS_TOOL_ROUTING=0           # show the model every tool again
JARVIS_TOOL_ROUTING_KEEP=10     # how many tools to shortlist per turn
```

## Commands

```bash
jarvis start                     # backend + voice + desktop overlay
jarvis stop                      # stop every Jarvis-owned process and service
jarvis restart                   # verified stop, then a clean GUI start
jarvis restart --headless        # restart without the desktop overlay
jarvis status

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
```

`bash scripts/hud.sh` starts the local HUD. `bash scripts/hud.sh voice` also starts the wake-word
loop. The web server binds to `127.0.0.1` by default.

If you move or rename the Jarvis folder, run `bash scripts/relocate.sh` once: the systemd
user units, the `jarvis` command on PATH, and the login autostart entry all store an absolute
path outside the repo, and it re-points whichever of them you have installed.

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
