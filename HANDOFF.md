# Jarvis — where things stand

`README.md` is the real documentation. This file covers what changed most recently, how far each
piece was actually taken, and what is next.

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
