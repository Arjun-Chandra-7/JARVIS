# Jarvis — where things stand

`README.md` is the real documentation. This file covers what changed most recently, how far each
piece was actually taken, and what is next.

---

## Seventh pass (2026-09-24): lessons on any subject; wrong answers found in the history

**Any subject.** `jarvis/teach/lessons/generic.py`: the strong model writes the lesson as JSON,
code validates and lays it out (flow / cycle / tree / layers / timeline / compare, edges routed
around boxes) and draws it; "explain this visually" over a video on any subject uses its
captions, over a page or app what is selected or in view. Live: water cycle with two follow-ups,
drift p95 4 ms, first frame 6.8 s after the request (ack spoken at once). Real-model runs: 6 of 8
topics built (OSI, Newton, French Revolution, transformer in Hindi, digestive system in pure
Hindi, water cycle); 2 hit Groq's per-minute cap. **Gemini returns `permission_denied` for the
configured key** — the only strong fallback — so check that key.

**Wrong answers from `~/.config/jarvis/hud-history.jsonl`, each fixed and pinned by a test:**

| Heard | Said | Cause → fix |
|---|---|---|
| "Is Clawed completed?" | "I'll send that message to the unknown number…" | the last WhatsApp message was attached to *every* turn, forever → only when recent (10 min) and the turn is about replying or names the sender |
| an unclear sentence | "Please clarify or use whatsapp_send to reply." | same, plus tool names spoken → sentences exposing tool names/calls are dropped from speech |
| "What values does this show about Nicola and Jippo?" (after the chapter on screen was explained) | "Who are these?" ×4 | screen answers weren't remembered → kept 10 min; follow-ups answered from the same material (`screen_follow_up` handler) |
| "यह step English में समझाओ" | "…will provide a succinct response directly…" | same |
| "Find a whiteboard site and draw me the Mona Lisa" | "[error] 'webSocketDebuggerUrl'" | drawing assumed Chromium; Zen is Marionette → drawn on the overlay instead |
| "Yeah, that's it." / "JARvis, that's it." | "Just handling routine tasks." | goodbye needed an exact sentence → lead-ins allowed |
| — | "(Spoken question, not a request…)" logged as the user's words | the note attached for the brain → stripped before logging |

**The test suite wrote to the real history** (314 fake turns: "Sent to the number ending 0001",
made-up requests), which the HUD restores on reload. Tests now use a temporary file
(`tests/conftest.py`); the 314 were removed from the real file, backup at
`~/.config/jarvis/hud-history.jsonl.bak-before-test-cleanup`.

Suite: 2522 → 2556 passing.

## Sixth pass (2026-09-24): the teaching overlay — drawing while explaining

Branch `live-failure-repair`. README → Explaining with pictures; design, measurements and limits
in `docs/TEACHING_OVERLAY.md`.

**Built:** a second, sandboxed, click-through Electron window over the work area
(`overlay/teach/`), driven over the existing `/emit` → SSE path; one protocol spec
(`protocol.json`) enforced by a Python and a JS validator that agree on every test case; SVG +
DPR canvas renderer with a timeline that only runs while something moves; `jarvis/teach/` —
lesson plans, a runner with generations, snapshots, pause/continue/back/skip/again/follow-ups
and auto-clear; Pythagoras (standalone or traced from the video frame) and RAG templates in
en / hinglish / hi / hi-pure; `local_tts.speak_segments` with a per-phrase `on_start`; the voice
hook before the fast path (`VoiceSession._teach_turn`); a typed handler first in the command
chain; manual pen; GNOME shortcuts `Ctrl+Super+Escape` (dismiss) and `Ctrl+Super+P` (pen) via
`scripts/install-teach-shortcuts.sh` — **installed on this machine**.

**Verified live on GNOME Wayland** (overlay run from this worktree, real Kokoro/Piper audio,
real Zen + YouTube): RAG with follow-ups; Pythagoras standalone in English and pure Hindi;
the YouTube lesson paused a playing video, verified the pause (0.33 s), grounded the topic in
the captions, and fell back to a clean triangle because the browser was on another workspace
(it now says so); interruption + continue; the emergency shortcut (real GNOME keybinding) and
pen shortcut; input region read from the X server (1 px when shown, full only in pen mode,
unmapped when hidden). Drift p50 1 ms / p95 5 ms; request → first frame 45 ms; ~144 fps.

**Not verified:** a spoken request through the microphone (the voice loop is tested with
scripted transcripts; the live runs used the same runner and TTS without the mic); tracing a
real teacher's triangle on screen; multi-monitor (one display connected); fractional scale on
the real desktop (rendered offscreen at 125 % and 150 % only).

**Deploy** — the services still run the main checkout's branch:

    git -C ~/Madara/Dev/Jarvis merge --ff-only origin/live-failure-repair   # or check the branch out
    systemctl --user restart jarvis-backend jarvis-voice
    kill $(cat /tmp/jarvis-overlay.pid); ~/Madara/Dev/Jarvis/start_jarvis.sh

**Side effects of this session:** the overlay was restarted from this worktree (still running
from it); two GNOME keybindings were added; a Pythagoras video tab was opened in Zen; an
MPRIS "play" briefly started the *Two Gentlemen of Verona* tab (~3 s) before it was paused again;
spoken test lessons played through the speakers.

---

## Fifth pass (2026-09-24): one conversation per wake word, barge-in, echo cancellation, the voice

Branch `live-failure-repair`. Details in README → Conversations, Talking over him, Echo
cancellation, Media, Hearing, Privacy, The voice.

**Built:** `conversation.py` is the single state machine (published to
`$XDG_RUNTIME_DIR/jarvis-conversation.json`; dictation suspends and resumes it). Barge-in
(`bargein.py`) on Silero speech above a tracked background, 2–4 frames, running from the moment a
request is sent; the interruption's onset is kept for the next capture; "go on" finishes the cut
answer; a false interruption while thinking re-asks under the same event id. WebRTC AEC as a
PipeWire client (`scripts/install-aec-service.sh`). Media ducking and verified MPRIS pause/play.
Local fast path: stop, cancel everything, go on, media keys. `/chat/stream` (questions only).
`speech_text.py` (numbers, links, secrets, maths, usernames, Hinglish → Devanagari for the Hindi
voice), `loudness.py` (per-line loudness, peaks held under the sink's cubic gain), Piper fallback
announced once, cached acknowledgements. Assistant STT is Groq first, local second.
`youtube_command.py`: search → play the Nth result without browser automation. Journal redaction,
`--voice-diagnostics N`, `--voice-report`.

**Verified, and how.** The real voice process (this checkout) was driven over *virtual* PipeWire
devices — synthetic speech (a different Kokoro voice) played into a virtual room, Jarvis speaking
into the canceller's sink so his own voice echoed back — against the real backend and Groq:

| | |
|---|---|
| The required flow, one wake word | open YouTube → search → play first → pause → Hinglish → barge-in → that's all: all handled, no second wake |
| Barge-in, his echo in the room (AEC) | voice onset → speech stopped 192–196 ms (detector 160 ms) |
| Barge-in, echo + lecture playing | 199 ms |
| Lecture saying "Hey Jarvis" ×3 through the canceller | 0 wakes (1 without the reference) |
| A person saying "Hey Jarvis" over the lecture at −10 dB | woke |
| Local command routing (transcript → handler) | < 0.1 ms warm, 58 ms first call |
| STT (Groq) | 0.25–0.43 s; Hinglish correct where local `small` took 13.7 s and garbled it |
| End of speech → first audio | 1.5 s local media key; 2.4–3.0 s backend actions (STT + backend + synthesis) |
| Suite | 2251 → 2360 passing |

**Not verified — the microphone is muted** (it was muted when this pass began; left as found).
Nothing here has heard a real voice in a real room: acoustic echo, speakers vs headphones, a real
lecture from the speakers, your own Hindi/Hinglish. The AEC service is written and tested on the
rig but **not installed**, and the running services still run the old checkout. To deploy:

    git -C ~/Madara/Dev/Jarvis merge --ff-only origin/live-failure-repair   # or check the branch out
    ~/Madara/Dev/Jarvis/scripts/install-aec-service.sh
    systemctl --user restart jarvis-backend jarvis-voice

**Found and fixed on the way:** the speaker volume at 153% (cubic gain 3.58) clipped 30% of voiced
frames; Roman Hinglish went to the English phonemiser; `/chat` never streamed to the voice; the
speaking flag was set while still thinking; an interruption between "give me a moment" and the
answer waited 1.3 s; "pause" went to the default MPRIS player, not the playing one; "search for X"
after "open YouTube" failed and "play the first video" became a web search for "first video";
the automation hint was spoken on every reply; "the" counted as Hindi.

**Side effects of this session, stated plainly:**
* An existing test (`test_dead_microphone`) called the real `unmute()`; the suite unmuted your
  microphone for about a minute. It was re-muted, and `tests/conftest.py` now refuses any test
  command that changes the real mixer or players.
* Early AEC measurements played ~1 minute of synthetic speech through the speakers (a test sink
  that turned out not to exist fell back to them).
* The end-to-end run's "wait, pause it" paused a Chromium MPRIS player that was reporting
  Playing. It was left paused.
* The runs opened YouTube and a Pythagoras video in Zen (silently — output was on a null sink).

## Fourth pass (2026-09-24): screen questions, teaching, long answers cut off

From the journal after dictation went live:

* **Long answers stopped halfway.** Barge-in fired on the lecture playing aloud (louder than
  Jarvis's echo). It now stands down while any MPRIS player is playing, and otherwise needs ~1 s of
  sustained sound. The wake word, push-to-talk and the dictation key still stop him.
* **Free-form screen questions** ("On my screen, what is a sequence output…", "…in this
  scenario?", "take a screenshot and explain…") went to the local model ("I can't see your
  screen"). Any question pointing at the screen/video/page/diagram is now `screen.ask`, answered
  from the screen, with general knowledge added and labelled when the screen does not cover it.
* **Topic explanations were one flat sentence** from the local 3B. `jarvis/explain_command.py`
  sends topic questions to the strong model as a tutor (idea → example → takeaway, ~130 words,
  the question's language) with ten-minute follow-ups that add new material. Personal, live and
  Jarvis-feature questions are not topics and route as before.

Verified live through /chat with the real brain and Groq: RNN explanation with an analogy and
example; "give me some examples" gave three new ones; "photosynthesis kya hota hai" in natural
Hinglish; a screen question answered from the playing video's transcript.

---

## Third pass (2026-09-23): system-wide voice writing (dictation)

`jarvis/flow/` — Right Alt (tap = hands-free, hold = push-to-talk, Escape cancels) dictates into
whatever field has the cursor. Details: `docs/DICTATION.md`.

**Reused:** the evdev key reader (extended to key-up and a cancel key), the voice process's
single microphone stream, the Silero-era capture loop's frame format, AT-SPI (a new persistent
focus bridge, `scripts/atspi_focus_bridge.py`), ydotool, Marionette for Zen read-back, the
overlay's event stream, the approvals-style "propose then confirm" for dictionary changes.
**Replaced:** the old spoken dictation's typing (`ydotool type`, which cannot type Devanagari and
logged every dictated sentence as a reply) — "Jarvis, dictate" now goes through the new engine,
and its transcripts are logged as "(dictated text)". **Added:** microphone coordinator,
cleanup, command grammar, profiles, dictionary, verified insertion, history, the capsule.

**Verified live on this machine** (real key through ydotool → evdev, real microphone hearing
speech from the speakers, the real voice process): tap-to-dictate and hold-to-dictate inserted the
exact sentences into GNOME Text Editor; Escape inserted nothing. Key → recording 52–158 ms,
release → transcript 0.24–0.37 s, insertion 45–57 ms. Through the engine with real STT and real
fields: GNOME Text Editor (English list, Devanagari), Zen textarea / contenteditable "WhatsApp"
compose / search box (DOM read back and matched exactly; Hindi speech came out as Roman
Hinglish in the chat box), password field refused with nothing transcribed, Ptyxis terminal
previewed then pasted with no Enter. Clipboard restored every time.

**Measured and fixed along the way:**
* The local fallback turned Hindi into English ("Today I want to learn about science") or
  nothing: `local_stt.transcribe` always primes with an English command vocabulary and drops
  low-confidence segments. Dictation has its own local path (detect language, English prompt only
  for English): Hindi CER 100% → ~24%, Devanagari kept. `base` writes Hindi in Urdu script;
  `small` is the default. `medium` fits only on the CPU here (Ollama holds the GPU): ~100 s/clip.
* Groq Whisper primed with one line of Roman Hinglish writes Hindi speech in Roman letters with
  English words as English ("Aaj electricity ka chapter revise karna hai"); unprimed it spells
  English words in Devanagari. Chats/search get Roman, everything else Devanagari (configurable).
* Firefox answers "ok" to an accessible insert into a contenteditable and changes nothing; its
  accessible text also never updates after a paste. Both handled (re-check, then paste; DOM read).
* GTK 4 text views and the terminal report length but read back ""; exact growth is the evidence.
* A browser keeps reporting its window "active" after another took focus; the focus bridge now
  follows focus and window-activate events from the moment the voice service starts.
* A lecture playing aloud falsely woke the assistant; while it thought (4.7 s) and spoke, a
  dictation key press waited behind it and the words were lost. Dictation now runs on its own
  worker started by the key: it cuts Jarvis's speech, takes the microphone lock (every loop
  reader holds it per frame) and records at once. Live, with that lecture still playing:
  key → recording 81–93 ms, release → transcript 0.25–0.26 s, English exact, Hindi near-exact,
  Escape kept nothing. (That run used JARVIS_DICTATION_DRY_RUN=1 — you had a chat focused.)

**Not verified:** VS Code (a separate instance never took focus while you were using Zen; the
code profile is covered by tests only), and a real WhatsApp compose box (a local look-alike page
was used, deliberately). Hinglish accuracy on synthetic speech is not meaningful — the Hindi TTS
voice mangles English words — so it needs your own voice to judge.

---

## Second pass (2026-09-23): bridge live, one approval system, semantic screen, YouTube

Suite: **1909 passing**.

### WhatsApp bridge — restarted and verified live

- `systemctl --user restart jarvis-whatsapp`: active; loaded the new code; logged "Forgot 11 chats
  mislabelled with the owner's name"; address book 2,521 → 2,510; no name now covers more than 3 chats.
- Live HTTP: foreign `Host` → 403; `Origin: http://127.0.0.1.attacker.example` → 403 (also on
  `POST /send`); the HUD's origin → 200.
- Papa flow against the live bridge, dry run: resolves to the right Papa in 23–147 ms.
- **New leak found and fixed:** libsignal (inside Baileys) printed whole Signal session objects,
  private keys included, to stdout → the systemd journal. 8,849 `privKey` lines in 30 days. The
  bridge now drops them; 0 after restart. **The old lines are still in your user journal**;
  clearing them means vacuuming it (`journalctl --user --rotate --vacuum-time=1s`), which also
  deletes every other user-service log — your call.
- `/tmp/jarvis-wa-debug.log` from before the fix still exists and holds message text; nothing
  writes to it now. Delete it when convenient.

### One approval system — `jarvis/approvals.py`

Replaces the blocking `confirm_fn` (which the web/voice path never had, so e-mail and calendar
always answered "user declined") and WhatsApp's private pending slot. E-mail, calendar, messages,
destructive shell, file overwrite, browser restart and the autonomy switch all propose, then run
the bound original call on "yes". Stable ids, fingerprints, 3-minute expiry, per-session,
ambiguity → a question, provider failures reported with the provider's words, audit without
bodies. Overlay: `GET /approvals`, `POST /approvals/{id}`.

Removed: `jarvis/agent/sdk_tools.py` (864 lines, no callers since the Claude SDK brain went; it
had an e-mail tool with no gate at all).

### Semantic screen — `jarvis/screen/`

`ScreenElement` (id, role, name, value, app, window, bounds, states, actions, parent/children,
source, confidence) from AT-SPI → DOM (CDP or Marionette, one `Page` interface) → OCR; actions go
through the element's own source and are verified by reading it again (`ActionResult.verified`).
Password fields are never read or typed into.

Environment here: **GNOME on Wayland**, XWayland at `:0`, no ydotool. AT-SPI works through system
Python (`scripts/atspi_snapshot.py`, now with states and values); native windows report every box
at 0,0, so native controls are only ever acted on through accessibility actions. Zen (Flatpak)
exposes almost nothing over AT-SPI — browser content comes from the DOM adapter.

### YouTube — verified live, and where it stopped

Driven against a real YouTube page in an **isolated** Zen (own profile, own Marionette port
2929, headless; your running Zen was never touched) and in Chrome via the DevTools MCP:

| What | Result |
|---|---|
| Title, time, duration, paused, caption tracks, ad | read correctly (20 ms) |
| Ad playing | detected; handler says so (39 ms) |
| Pause | verified on the player, both engines |
| Same page scripts under Marionette and CDP | both ran unchanged |
| Real handler through `commands.handle` | 10–39 ms to the deterministic answer |
| Grounded explanation, real model | 0.6–1.3 s on Groq `qwen/qwen3.8-27b`; Hinglish when asked in Hinglish; says when the excerpt does not justify a step |

**Not verified — YouTube refused:** in both automated, signed-out sessions the player showed
"Something went wrong" and never loaded media (`navigator.webdriver` is true under automation),
and the transcript routes returned nothing: the caption URL needs a proof-of-origin token, a
replay of the player's own tokenised request came back empty, and `get_transcript` answered
"Precondition check failed". The adapter now detects the player's error overlay and says it,
and "play" only counts when the clock advances. **Whether transcripts and playback work in your
real, signed-in Zen after "restart the browser with control" is the open question** — a
Marionette-controlled browser also reports `navigator.webdriver = true`.

Model configuration found broken: Gemini returns 403 ("project has been denied access") and the
configured Groq model `qwen/qwen3.6-27b` is retired (404). Teaching now uses a "strong" tier that
falls back to `qwen/qwen3.8-27b`; the local brain (`qwen2.5:3b`, 10.8 s) explained Pythagoras as
"the sum of the sides". Update `JARVIS_GROQ_MODEL` in `.env` when convenient.

---

## First pass (2026-09-23): audit, security, messaging, notifications, conversations

Suite: 1683 passing before, **1829 passing** after (146 new tests, none removed or loosened).

### Security — fixed

| Finding | Fix |
|---|---|
| `enable_full_laptop_autonomy` let the **model** switch off its own shell/file confirmation gate — one injected webpage or message away from an unconfirmed shell | enabling now requires the person's confirmation; disabling is always allowed |
| `control_laptop_full` ran `shell=True` **outside the sandbox**, a second shell path around everything `run_bash` guards | it now goes through `run_bash` (same gate, same bubblewrap policy) |
| `read_file` / `write_file` could reach `~/.ssh`, `.env`, tokens — read a key, then fetch a URL with it | `sandbox.is_secret_path` refuses the sandbox's masked paths and key/env files |
| WhatsApp bridge accepted any origin **starting with** `http://127.0.0.1` — `http://127.0.0.1.attacker.example` could send messages cross-site | exact origin allow-list |
| No `Host` check on the web server or the bridge — a DNS-rebound page could read `/whatsapp/inbox` and `/chats` | both reject a non-local `Host` |
| Bridge wrote every message's text to a world-readable `/tmp/jarvis-wa-debug.log` | opt-in with `WA_DEBUG=1`, file mode 0600 |
| A 3-second microphone recording (`74`, at the repo root) was committed in `1f846af` (2026-09-16) and **pushed to `origin/main`** | untracked; runtime audio patterns added to `.gitignore`. It remains in history on GitHub — removing it needs a history rewrite and force-push, which is a separate decision |

### Known, not yet fixed

- ~~The main voice path cannot confirm anything.~~ Fixed in the second pass (approvals.py).
- `permissions._DESTRUCTIVE` is a denylist (it says so). The sandbox is the real boundary.

### Messaging — the "Papa" failure, root-caused

Three separate causes, each reproduced against the real address book (names only, no numbers
printed):

1. **The bridge named chats after the owner.** It recorded `pushName` on outgoing messages,
   where it is the owner's own name, so every chat the owner wrote to was relabelled with it —
   eleven chats carried one name. Fixed at the source, and existing mislabels are dropped on
   connect (`forgetOwnName`).
2. **Two "Papa"s.** The phone book and the bridge each had a "Papa" with a different number; the
   bridge's was somebody's self-chosen profile name. The old code sent to whichever store it
   checked first. The resolver now ranks owner-saved names over bridge-only profile names, and
   the bridge no longer lets a profile name overwrite an address-book name.
3. **Fuzzy auto-send.** A single substring or shared-word match was sent to without asking
   ("man" → a stored "Pradyumna"). Now only one clear match above the threshold is sent to.

Also: local numbers are sent with the country code (they were passed to WhatsApp bare), and
"Sent" is reported only when the bridge returns a chat and a message id.

Verified: real address book + live bridge in `JARVIS_DRY_RUN_SENDS=1`: "message Papa on WhatsApp:
…", "papa ko bol dena …", "text dad: …" all resolve to the right Papa in 20–260 ms without a
model call. **The running bridge service still has the old code until it is restarted**
(`systemctl --user restart jarvis-whatsapp`).

### Notifications

`jarvis/notifications.py`: normalise → dedupe → policy → group. Wired into the voice session;
a test drives the real `_handle_phone_event` with seven messages and checks one announcement.
Not yet heard on real hardware.

### Conversations

`jarvis/audio/conversation.py`: the state machine (IDLE … FOLLOW_UP_WINDOW … SLEEPING) and the
"is this for us" judgement. The follow-up window is **on by default now** (8 s). The voice loop
itself still has no automatic test — it needs a microphone — so the rules are tested and the
loop wiring was read, not run. Barge-in exists (`JARVIS_BARGE_IN`) but does not yet report into
the state machine.

Two voice-loop bugs fixed: "what is impulse" contained "pulse" and got a system status report;
"open PhonePe" contained "open phone" and mirrored the phone.

### Measured

| | |
|---|---|
| "Message Papa on WhatsApp: …" → resolved + previewed (dry run, 2,850 contacts) | 22–260 ms |
| Test suite | 1829 passed in 46 s |

Not measured yet: wake → first word with the new follow-up window; the <200 ms target for
deterministic actions end to end through the voice loop.

## Next, in priority order

1. Confirm the YouTube slice in the real signed-in browser (manual sequence in the chat log).
   If `navigator.webdriver` blocks YouTube there, drive Chromium through the extension relay,
   which does not set it.
2. Move `screen_command.py` / `find_and_click` onto `ScreenModel` so every click is verified.
3. Dictation rewrite (hold/toggle hotkey, raw vs polished transcript, clipboard restore).
5. TTS clarity audit and benchmark; teaching overlay; away-mode hardening; self-repair.

---

# Earlier passes

### Verified by watching it work

Not "the tests pass" — these were each driven against the real thing and the result observed.

| What | How it was checked | Result |
|---|---|---|
| Browser control on Zen | Drove a real isolated Zen over Marionette | navigate, read, click, type, close all work |
| Study mode | Opened a Shorts tab and an ordinary tab in a real browser | the Short closed, the other stayed |
| Shorts blocking | Opened the Shorts feed and read where it landed | a real Short keeps `/shorts/`, caught by URL |
| Microphone | Recorded from the real device | unmuted, RMS 0.0137, peak 0.178 |
| Speech in and out | `python -m jarvis --selftest` | spoke, heard, transcribed "Jarvis, how are you?" at rms 1710 |
| Streaming a reply | Real model (local Ollama) | reassembles exactly; a tool call forwarded zero fragments |
| Time to first word | Real model and real synthesis | 3-sentence answer 0.76 s sooner, 8-sentence 3.20 s sooner |
| The whole program | `python -m jarvis --text` on the local brain | answered correctly |
| The overlay | Launched this branch beside the running one, captured the window | renders light |

The isolation mattered for the browser work. `flatpak run` on an already-running app hands its
arguments to the existing instance, so `--profile` is silently dropped and a test connects to
the real browser with all its tabs. Two defences: `--new-instance`, and a Marionette port of its
own, because the real browser already holds 2828.

### Measured

| | before | after |
|---|---|---|
| Routing recall@10 | 87.7% | 100% (and 100% on 17 held-out phrasings) |
| Time to first spoken word, short answer | 2.11 s | 0.96 s |
| Time to first spoken word, 8-sentence answer | 5.73 s | 2.53 s |
| Microphone noise floor | 0.84 RMS (saturated) | 0.0052 |
| Idle overlay CPU | 37.3% | 0.59% |

### Known limits, stated plainly

- **Streaming is only measured against a local model.** The path is the same for the hosted
  brains, which are OpenAI-compatible, but the numbers above come from Ollama. A provider that
  will not stream falls back to waiting for the whole reply.
- **The hosted voice does not stream.** It is asked for whole utterances, so it behaves exactly
  as it did before.
- **The voice loop itself is not covered by automatic tests.** It needs a microphone, a speaker
  and a wake word. `--selftest` is the check; the seam between the brain and the speaking is
  unit-tested, the loop around it is not.
- **Tab closing needs a drivable browser.** An extension or the debug port for Chromium, a
  restart with automation for Firefox. Without it study mode can judge a tab and not close it.

### If something looks wrong

Start with `python -m jarvis --check` (dependencies, models, audio devices — no API calls), then
`--selftest` for one live round trip through the microphone and speaker.

If the overlay comes up black, the checkout is probably behind: the light theme is recent. A
compositor that refuses a transparent visual is the other cause, and the window now falls back
to white rather than black so that failure is no longer mistaken for the first one.

If Jarvis cannot hear you, just ask it — "is my mic working". It will say, and unmute it if that
was the trouble.
