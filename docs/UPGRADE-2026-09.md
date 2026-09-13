# Jarvis upgrade — September 2026

What changed, why, and what still needs you. Branch `worktree-jarvis-1000x`, 21 commits,
44 files, +5.6k/−0.7k lines, 344 tests passing (was 231).

---

## The short version

Three bugs were quietly breaking the assistant's core loop, and none of them looked like bugs
from the outside:

1. **The brain could not pick a tool.** All ~80 tool schemas went out on every request with their
   descriptions *deleted* — `build_registry` stripped them and `groq_core` clipped whatever
   survived to 40 characters. A 3B model was choosing between eighty bare function names. Asked
   *"What is my CPU usage right now?"* Jarvis replied **"None of the provided functions are being
   called"** instead of calling `system_stats`.
2. **Gmail, Calendar and Tasks were invisible.** The registry gated them on a `credentials.json`
   in the vault — a path Jarvis has never written. Eight tools were dropped before the model saw
   them, on a machine that was fully authorised.
3. **Semantic memory was dead.** `embeddings.available()` returned true because Ollama was
   running, but `nomic-embed-text` had never been pulled, so every embed call returned `None`.
   Recall had silently been keyword-only, and `--index` was building an empty index.

All three are fixed and verified. The same CPU question now returns real telemetry in ~4s.

---

## What was rebuilt

### The brain

| Change | Effect |
|---|---|
| `agent/tool_router.py` — BM25 + embedding retrieval, fused with RRF | 3662 → **238 tokens** of tool schema on a CPU query, with full descriptions restored |
| Parallel execution of read-only tools | Calendar + inbox + weather in one batch costs the slowest, not the sum |
| LinkedIn intent classifier gated on topic | Every message used to pay for an extra blocking LLM round-trip |
| Streaming replies (`/chat/stream`) | First word at **1.06s** instead of a blank screen until 1.64s |
| Rolling conversation summary | Trimmed turns are folded into a summary instead of deleted — "book it for the time we said" now resolves |

### Memory

`memory/store.py` replaces a JSON blob plus a ripgrep pass with one SQLite file: chunks, an FTS5
mirror for BM25, and float32 vectors, **fused with RRF** so the two signals inform each other.
FTS5 ships inside Python's sqlite3, so this removed a dependency rather than adding one.

- **Episodes** — what was said, by whom, when. Makes "what did we discuss on Tuesday" answerable.
- **Facts** — durable distilled statements, retrieved directly instead of reconstructed.
- **Automatic retrieval every turn.** Waiting for the model to call `recall` never worked; a 3B
  model essentially never does.
- **Nightly consolidation** (`--consolidate`, 03:15 from the daemon) distils episodes into facts
  and supersedes contradictions, so memory compounds instead of accumulating.

Verified across a full restart with `chat-history.json` deleted: told the flight details, killed
the process, and a cold backend still answered *"seat 14C"* from the episode store alone.

### Voice

**Silero VAD** (`audio/neural_vad.py`) replaces the RMS threshold. Loudness is a poor proxy for
speech: a keyboard, a desk knock and the fan all clear an energy bar. Checked against the real
network — digital silence, white noise and a pure 800 Hz tone all score below 0.002, and energy
VAD fires on two of those. 2.3 MB, ONNX, runs on the CPU next to everything else.

The HUD's waveform is also no longer synthetic — `vad.record_utterance` publishes real microphone
RMS, so the meter shows the actual room.

### Perception

- **Screen reading fixed.** *"Tell me what's on my screen"* returned the single word
  `xtrascript`. The capture was fine; moondream at 1.9B is a captioner, not a VQA model — it
  answers "describe this" well and "what app is open?" with garbage. The vision model now
  describes and the language model answers from that description.
- **`what_am_i_doing` / `GET /context`** — focused window, open apps, idle time, whether a call is
  holding the screen awake, what is playing. GNOME on Wayland hides most of this; this uses what it
  does give up (`wmctrl -lx`, `_NET_ACTIVE_WINDOW`, Mutter's IdleMonitor, `IsInhibited`) and is
  honest that native-Wayland windows are invisible to it.
- **Announcements are held during calls.** Nothing is dropped — it still reaches the HUD and
  `catch_up` — it just waits.

### The overlay

Rebuilt against three specific criticisms:

- **`backdrop-filter` was a no-op.** Over a fully transparent window there is no page content
  behind the panel to sample, so `blur(18px)` blurred transparent black. The "glass" was a flat
  dark rectangle. Depth now comes from a layered ground, a gradient rim that is brighter where
  light would fall, and a real shadow.
- **Nothing loops at constant velocity.** Three rings spinning forever at 4s/2.8s/6s is the
  clearest tell of costume sci-fi. `overlay/reactor.js` is one WebGL2 quad of signed-distance
  fields where every moving thing is a measurement: four arcs sweep clockwise from twelve o'clock
  by CPU/memory/GPU/battery, the ring deforms with live microphone energy, and the one continuous
  animation exists only while work is genuinely in flight. No three.js, no CDN — the GPU is
  already carrying Whisper, Piper, Ollama and a headless Chrome.
- **Seven icon buttons with no keyboard path**, in a voice-first product. The text input is now the
  single entry point: it filters commands and recent turns, Enter runs the top hit, Ctrl+K opens
  an action panel in the browser top layer, and typing anywhere drops you into it. Mode switching
  runs through `startViewTransition`, so the reactor travels into the panel header.

Orbitron is gone from both surfaces; numerals use tabular mono so the clock stops jittering. The
ambient layer was brought into the same language.

`/suggestions` now reflects reality — a flat battery outranks a failed build, which outranks
unanswered messages — instead of returning the same six strings at 3am as at 9am.

---

## How to check it yourself

```bash
.venv/bin/python -m pytest -q                  # 344 tests
.venv/bin/python -m jarvis --check             # deps, keys, audio, speech detection
.venv/bin/python scripts/audit.py              # exercises every subsystem for real
xvfb-run -a node scripts/overlay-smoke.js      # loads the real overlay, reports what came up
.venv/bin/python scripts/hud-shot.py hud.png --demo   # photograph the HUD
```

`audit.py` is the one to trust. It calls the real endpoints and dispatches the real read-only
tools; anything with side effects is reported as **explicitly skipped** rather than passed, so it
never overstates what it verified.

**Current state on this machine: 70 ok, 4 warn, 0 fail.**

---

## What needs you

**One thing, and it needs a browser:**

```bash
.venv/bin/python -m jarvis --google-auth
```

Your Google refresh token is dead (`invalid_grant`). All four remaining warnings are this one
cause. It is not a code fault — Google expires the refresh token of an OAuth app left in
**Testing** mode every seven days. Publishing the app in the Cloud Console stops it recurring;
otherwise this will need doing weekly.

Jarvis now says this out loud instead of shrugging: the tools return the exact command, the HUD
health dot goes amber with the fix in its label, and "Reconnect Google" is the first suggestion.

---

## Deliberately not done

- **Sentence-by-sentence TTS.** The plumbing is in place (`/chat/stream` works, `on_delta` is
  wired through the agent), but speaking while generating means restructuring the audio queue and
  barge-in, and I cannot test a microphone from here. Shipping untested changes to the part you
  use every day is the wrong trade. This is the highest-value next step.
- **Electron 42.** The overlay is built for the Chromium 128 you have and picks up squircle
  corners and anchor positioning automatically via `@supports` if you upgrade. Do **not** go to
  43+ — `setIgnoreMouseEvents(true)` regressed there and the transparent window would swallow
  every click.
- **The browser HUD (`webui/`).** Left working as a fallback. The overlay is the primary interface.
- **AT-SPI screen control.** Would be strictly better than pixel vision, but needs
  `sudo apt install python3-pyatspi gnome-ponytail-daemon` and `toolkit-accessibility true`.

---

## New knobs

| Variable | Default | Effect |
|---|---|---|
| `JARVIS_TOOL_ROUTER` | `1` | `0` restores sending every tool schema |
| `JARVIS_TOOL_K` | `14` | tools retrieved per turn |
| `JARVIS_NEURAL_VAD` | `1` | `0` reverts to the energy threshold |

New commands: `--consolidate`. Changed: `--index` now builds the SQLite store and deletes the old
flat index.
