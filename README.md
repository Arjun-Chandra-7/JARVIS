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
- Makes pictures. "Generate an image of a samurai in bamboo" writes a file and opens it.

## Study mode

"Jarvis, study mode." It closes the distracting apps and tabs and keeps them closed — a sweep
every eight seconds, because a YouTube Short is often over in twenty.

YouTube is judged rather than blanket-closed, since study mode that shuts the lecture is study
mode nobody turns on:

    Shorts          closed, always, before the title is even read
    a lecture       left alone — "Class 10 One Shot", "NCERT solutions", a derivation
    a vlog          closed
    unclear         left alone, and asked about once

Instagram, Netflix, Twitch and the rest close on sight. Ordinary sites are untouched; this is not
a firewall.

The bias is deliberate and it runs through every layer: when nothing is confident, the tab stays
open. Closing a lecture somebody is midway through is a much worse failure than leaving one
distraction up.

Entering study mode also opens ChatGPT with a standing exam brief — answer as an NCERT-grounded
CBSE examiner would, sized to the marks, in the textbook's terminology. The brief is
`jarvis/modes/exam_tutor_prompt.md`, a plain markdown document: edit it to change how the tutor
behaves. It is sent once per session and forgotten on the way out, so the next session starts a
fresh conversation with the brief at the top.

Say "exit study mode" to stop.

## Finding your own files

`read_file` and `list_dir` want you to already know where something is. `find_document` searches
your documents by what is written in them — "find that PDF about the hackathon" — across
`~/Documents`, `~/Downloads` and `~/Desktop` by default, which `JARVIS_DOCUMENT_ROOTS` overrides.

Ranked with BM25, no index on disk: a full fresh scan of the real folders takes about 1.5 s with
poppler doing the PDFs, which is quicker than deciding whether a cached index has gone stale.

Worth knowing before you use it: `~/Downloads` is where a browser drops identity documents, and
indexing it puts their text in an index. There is no filename heuristic for "sensitive" here,
because that is a denylist and one that is wrong once is worse than no promise. Point the roots
somewhere else if that matters.

## The memory vault, and facts that stop being true

Recall runs both halves of a hybrid search — embeddings for paraphrase, BM25 for the proper nouns
embeddings are bad at — and merges them by rank rather than by score, since a cosine and a BM25
score are not comparable numbers. Results both halves found are marked, because that agreement is
what the ranking is built on.

Facts can now carry a window:

    - Machine: ThinkPad X1 <!-- until:2026-03-04 -->
    - Machine: Bhramastra <!-- since:2026-03-04 -->

So "what is my machine" and "what did I have in January" are both answerable from one file.
Obsidian renders neither comment; an unstamped line is true and always has been, so nothing in
the existing vault needs changing.

The embedding model behind both is `JARVIS_EMBED_MODEL`, defaulting to `nomic-embed-text`. It is
worth experimenting with — the one controlled study in this area found swapping only the
embedding model moved accuracy 6.2 points — and `tests/test_tool_routing.py` is the instrument to
judge a change with.

## Messaging people

"Message Papa on WhatsApp: I'll be home by eight" is parsed deterministically — recipient, platform
and message are separated by the shape of the sentence, and the message goes out exactly as
spoken. Hinglish works the same way: "papa ko bol dena late aaunga".

Who "Papa" is comes from one resolver (`jarvis/integrations/contacts.py`) over three address
books: people you told Jarvis about (with aliases such as "Papa" or a nickname), the phone's
contacts synced by KDE Connect, and the names the WhatsApp bridge has learned. It sends only
when exactly one person clearly matches. A contact that merely *contains* the word ("Papa Johns
Atlanta") never matches, "dad" finds a contact saved as "Papa", two different numbers saved
under one name is a question, and a WhatsApp profile name somebody chose for themselves never
outranks a name you saved. Local numbers get the home country code
(`JARVIS_DEFAULT_COUNTRY_CODE`, default 91) before WhatsApp is asked about them.

"Sent" is said only after the bridge returns the chat the message went to. Each send is logged
to `Jarvis/private/outbox.jsonl` with a masked number and a hash — never the text.

    JARVIS_SEND_APPROVAL=new     preview the first message to someone new (default)
                         always  preview every message
                         never   send once the recipient is certain
    JARVIS_DRY_RUN_SENDS=1       resolve and preview, never send (for development)

A held message goes out on "send it" / "haan bhej do" and is dropped on "cancel" / "rehne do".
A bare "ok" does neither.

## Notifications

Incoming messages are grouped before they are spoken: a conversation is announced once it has
been quiet for five seconds (at most twenty), so seven Instagram messages from one person are
"Seven new Instagram messages from Arjun. The latest says: …". Senders are said as names:
numbers become the saved contact or "an unknown number", handles like `arjun.chandra_07` become
"arjun chandra", and links, order numbers and tracking codes are left out of what is read.
The same message arriving from two sources is said once.

    "Don't announce Instagram for two hours"      "Only interrupt me for family"
    "Stop reading messages from this group"       "Summarise my notifications"
    "Stop reading messages from Rohit"            "Read that again"

Muted things are not lost; they go to the summary. Messages with "urgent", "call me",
"emergency" and the like are never held back.

## Conversations

Say the wake word once; after each reply Jarvis keeps listening for eight seconds
(`JARVIS_FOLLOW_UP_S`) without it, so "open YouTube" → "search Pythagoras theorem" → "play the
first video" is one conversation. In that window a transcript made only of fillers ("hmm",
"okay"), Whisper's silence hallucinations ("thanks for watching") or a single non-command word is
treated as the room and ignored — unless Jarvis just asked a question, in which case "yes" or
"Painter" is the answer. "That's all", "thanks Jarvis" or "bas" ends it; so does silence.
`JARVIS_FOLLOWUP=0` goes back to one wake word per turn.

## The voice

Kokoro, 82M parameters, Apache-2.0, on the processor. Measured here: the model loads in 1.0s and
synthesises at about 2.4x realtime, 24 kHz. Fifty-four voices; the default is `bm_daniel`,
British male. Set `JARVIS_KOKORO_VOICE` to another installed voice if you prefer it.

Kokoro has no emotion conditioning, and nothing here pretends otherwise. What it has is a speed
control, and that is enough for *delivery* — four of them, picked from the words before any model
sees them:

    neutral   answers and readings, most of everything
    brisk     acknowledgements; "on it, sir" should not be savoured
    grave     failures and warnings, where slowing down is the signal
    warm      greetings and goodbyes, the two lines a day that are not work

Trouble is checked before greeting, so "Good morning, sir. The overnight backup failed." is read
as a failure rather than as a greeting.

### How soon it starts talking

The number that decides whether an assistant feels quick is not how fast it speaks but how long
it says nothing, and there is never anything on screen to explain that gap. Three things were
costing it, all measured here:

    the voice loaded on the first reply        1.04s   now loaded at startup
    the whole reply synthesised before a word  1.67s   now the opening clause first, 1.05s
    the whole reply written before a word      up to a few seconds, now overlapped

The last is the big one. A reply used to be spoken only once the model had finished writing it,
so the silence contained the entire generation. Now the sentences are spoken as they are
written. Measured end to end against a local model: a three-sentence answer starts 0.76s sooner,
an eight-sentence answer 3.20s sooner — 56% of the wait. The saving is the generation time of
everything after the first sentence, so it grows with the answer, which is the right way round.

A turn that calls a tool speaks nothing while it runs. A model that says "let me open that for
you" and then calls a tool must not have said it out loud, and unlike a mistake on screen that
cannot be taken back.

The weights are 338 MB and are not fetched automatically. Until they are on disk the voice stays
Piper, so Jarvis cannot promise a voice it has no way to produce:

    .venv/bin/pip install kokoro-onnx
    mkdir -p ~/Madara/.cache/kokoro && cd ~/Madara/.cache/kokoro
    base=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
    curl -L -O $base/kokoro-v1.0.onnx && curl -L -O $base/voices-v1.0.bin

## Focus and coding setup

"Study mode" closes Netflix, Instagram, YouTube Shorts and noneducational YouTube tabs, and
keeps closing them until "exit study mode". YouTube videos are judged from their page titles;
ambiguous titles are left open, because shutting a lecture somebody is midway through is the
worse mistake — it is the one that gets study mode turned off for good. It also opens ChatGPT in
whichever browser you use and sends the exam tutor instructions from
`jarvis/modes/exam_tutor_prompt.md`. The mode flag survives a Jarvis restart.

Shorts are blocked by their URL, before the title is read, so nothing can argue its way past it.
Worth knowing, because it looks like a hole and is not: YouTube redirects `/shorts/<id>` to
`/watch?v=<id>` when the id is not actually a Short, and such a tab is then judged on what it is
like any other video.

Closing tabs needs the browser to be drivable — see below. That is the extension or the debug
port for a Chromium browser, and a restart with automation for a Firefox one.

"Open my coding setup" asks every other window to close, then launches an empty VS Code window,
Spotify and ChatGPT in whichever browser you use. Applications with unsaved work may ask before
they close.

Claude completion alerts come from Claude's Stop hook. Install it once with
`.venv/bin/python scripts/install-claude-stop-hook.py`. The passive coding activity display never
reads the terminal or clipboard and never announces a completion based on CPU use.

## Which browser, and whether it can be driven

Pages open in whatever you actually browse with: `JARVIS_BROWSER` if you set it, otherwise the
desktop default, otherwise whatever is installed. No favourite is baked in.

*Driving* a page — clicking inside it, reading it, typing into it — needs an automation protocol,
and the two browser families have different ones:

    Chromium (Chrome, Brave, Opera, Vivaldi)   the DevTools protocol
    Firefox  (Zen, Floorp, LibreWolf, Firefox) Marionette

Both are supported. Neither is switched on by default, and Jarvis will tell you which one it
needs rather than silently failing to click:

- **Chromium** — load `browser-extension/` once (see below), or start the browser with its debug
  port.
- **Firefox and Zen** — the browser has to be started with `--marionette`. Say *"restart the
  browser with control"* and Jarvis will do it. Firefox restores your session, so it costs a few
  seconds of flicker rather than your tabs.

## Driving the browser you already have open

Control normally needs the browser started with `--remote-debugging-port`, and restarting it to
get that costs every open tab. `browser-extension/` removes the trade: an extension holds the
same protocol from inside the browser, with no port and no restart. One unpacked load, then
Jarvis prefers the native port when it is there and this when it is not. See
`browser-extension/README.md`, including what you are trusting it with.

## Pictures

SD-Turbo, one step, on the processor, offline and free. About 8 seconds for a 512px picture once
the model is warm, 14 from cold. "Detailed" buys four steps and costs 25s; "a wallpaper" buys
768px and costs 14s; neither happens unless it is asked for.

It runs on the processor because it was measured not to fit on the card: 802 MiB free with Jarvis
up, against the 2.5 GB the model wants, and Ollama and Whisper both have to stay resident. The
stock decoder cost about twenty of the original 23 seconds, so it is replaced by the tiny
distilled one — 5 MB, and the whole picture drops to 7.5s.

The weights are 2.5 GB and are not fetched automatically. Until they are on disk, the picture tool
is not offered to the brain at all, so it cannot promise a picture it has no way to make:

    HF_HOME=~/Madara/.cache/huggingface .venv/bin/python -c \
      "from huggingface_hub import snapshot_download as d; \
       d('stabilityai/sd-turbo', allow_patterns=['*.json','*.txt','*/*fp16*','tokenizer/*','scheduler/*']); \
       d('madebyollin/taesd')"

Loaded, the model holds 5.4 GB of memory, so it is released five minutes after the last picture;
reloading it and making another costs under seven seconds. Generation takes six of eight cores,
leaving two for Whisper to keep hearing you — measured, that costs the picture nothing.

## Coding, in the editor where you can watch it

Say "ok, but now we need to add a dark mode toggle" with VS Code in front of you. Jarvis says
"switching to VS Code" if it has to move it, opens a terminal, starts a coding agent in it, and
types the prompt — visibly, so you can see which agent it picked, what it was asked, and take the
keyboard back whenever you like.

Claude, then Codex, then Antigravity, skipping any that is under 15%. The models come from the
efforts, because each agent only offers certain models at certain ones:

    claude   low, medium -> opus 5       high -> sonnet
    codex    low, medium -> gpt-5.6-sol  high -> gpt-5.6-terra
    agy      high only   -> gemini 3.8

How hard to think is read from the request — "fix a typo" is not "why is the websocket dropping
under load" — and is then fixed for the conversation. Follow-ups go to the same terminal, so the
second request still knows what the first one learned.

**Balances are real numbers.** `claude --print "/usage"` answers from outside a session:

    Current session: 44% used · resets Sep 18, 2am
    Current week (all models): 46% used · resets Sep 22, 2:29pm

The fuller window decides, so that is 54% left. Codex will not answer from outside — `codex exec
"/status"` reads the slash command as a prompt and summarises the repository instead — so its
`/status` is typed into the live terminal and read back off the screen, through the clipboard
rather than by OCR, because a terminal is exactly where OCR is worst.

**At 5% the agent is retired rather than cut off.** It is asked what it changed and what is left,
those notes go to the next agent as a handover, and the reply names both: "Claude was down to 4%,
so I took its handover notes and started Codex on gpt-5.6-terra."

**ChatGPT writes the prompt.** Spoken requests leave out the repository, the framework and every
constraint you had in mind and did not say. Jarvis sends only facts — the agent, the model, the
effort, the workspace, and your words — and your ChatGPT custom instructions decide how the prompt
is written, so that lives somewhere you can edit without touching this code. If ChatGPT is
unreachable your words go through unchanged and the reply says so.

**You get told what happened.** When the agent stops writing, what it concluded is read out, and
a dev-server or deployment link it printed is opened.

## Fifteen specialists, one model

A request is scored against fifteen specialists — desk, scribe, coder, researcher, scheduler,
messenger, librarian, analyst, navigator, artist, watcher, planner, tutor, companion, guardian —
and the turn runs under whichever one it belongs to, with that specialist's instruction,
temperature and tools in front of the model.

They are not fifteen sets of weights. Fifteen resident models cannot exist on 4 GB of video
memory, and anything destructive routes to the guardian regardless of score. The routing is
scored rather than classified by a model, so it answers in microseconds and cannot hallucinate a
specialist. See `jarvis/brains/roster.py`.

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

Jarvis reads browser pages from the live DOM of whichever browser you use, when browser control
is enabled — through the DevTools protocol for a Chromium one and Marionette for a Firefox one.
For native
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

## When the microphone is the problem

Ask it: "is my mic working", "check my microphone", "is my mic muted". It listens, says what is
true, and unmutes it if that was the trouble — rather than leaving you to work it out from
repeated "sorry sir, I didn't catch that".

The two ways to get this wrong are opposites. An idle probe of a healthy microphone in a quiet
room is near-silent by definition, so quiet is never reported as broken. And saturation is named
explicitly, because it is the failure that looks like working: with the input boosted far enough
the level meter moves and every word arrives as noise. This machine was found at 0.84 where a
healthy floor is 0.005.

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
