# Jarvis — where things stand

`README.md` is the real documentation. This file covers what changed most recently, how far each
piece was actually taken, and what is next.

---

## Latest pass (2026-09-23): audit, security, messaging, notifications, conversations

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

- **The main voice path cannot confirm anything.** Voice uses the web server's agent, which is
  built with `confirm_fn=None`, so every `_confirm(...)` there answers *no* — e-mail sending and
  calendar creation can never be approved by voice. Messaging now uses a turn-based approval
  ("send it") instead; the other confirmations need the same treatment.
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

1. Turn-based approval for the other confirm-gated tools (e-mail, calendar) on the voice path.
2. Semantic screen model (`ScreenElement` over AT-SPI → CDP/Marionette → OCR → vision);
   `integrations/accessibility.py` and `desktop_control.py` are the starting points.
3. Screen-aware study questions (video transcript at the current time via CDP).
4. Dictation rewrite (hold/toggle hotkey, raw vs polished transcript, clipboard restore).
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
