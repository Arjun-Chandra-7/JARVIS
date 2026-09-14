# JARVIS upgrade — checkpoint

Living record: what is done, what was decided and why, what was measured, and what is still open.
Written so a context reset never forces another full audit.

Branch: `worktree-fix-folder-move-paths`
Research and adoption record: [`RESEARCH.md`](RESEARCH.md)

---

## 0. Folder move repair — DONE (`191504d`)

`~/Dev` moved to `~/Madara/Dev` (`~/Madara` is a separate 954 GB NVMe). Everything storing an
absolute path outside the repo broke: five systemd units, `~/.local/bin/jarvis`, the login
autostart entry, the LinkedIn and Viralyst units, and **48 venv console scripts**.

The venv shebangs mattered most — `piper` is a console script, so **local TTS was failing with
ENOENT and Jarvis could not speak at all**.

`scripts/relocate.sh` now re-points installed integration at a given checkout, rewriting only what
is already installed and never changing enable/disable state.

Still broken, unrelated, **not fixed** (needs its owner): `tunnel-client.service` has
`EnvironmentFile=/home/xor_sensei/.env`, which does not exist.

---

## 1. Environment (verified 2026-09-14)

Ubuntu 26.04, kernel 7.0.0-31, **Wayland/GNOME**. Ryzen 5 7235HS (4c/8t), 22 GiB RAM,
RTX 3050 Laptop **4094 MiB VRAM** (~570 MiB already used by the desktop).
Root `/` 167 G with **38 G free**; `~/Madara` 938 G with **926 G free**, writable.
Ollama models live on the **root** disk at `/usr/share/ollama/.ollama/models`.
**No passwordless sudo**, so the Ollama store could not be moved (command is in RESEARCH.md).

---

## 2. Before / after — measured on this laptop

### Voice: end of speech to first spoken word

| Stage | Before | After | How |
| --- | --- | --- | --- |
| Endpoint decision | 2000 ms fixed | **~90 ms** | Silero VAD v5 (already inside faster-whisper) |
| Transcription | 1.70 s (`small.en`, beam 5) | **0.55 s** (`base.en`, greedy) | measured, n=9 |
| First audio out | 1440–1730 ms | **150–191 ms** | Piper voice kept loaded, streamed per sentence |
| **Total before the brain** | **≈ 5.1 s** | **≈ 0.8 s** | |

Plus a live partial transcript (`tiny.en`, ~440 ms/update, off the capture thread) shown as
provisional until the committed transcript replaces it.

### Tool calling — 17 representative commands, scored on tool **and** arguments

| | Correct | Median |
| --- | --- | --- |
| Before (all 84 schemas, clipped descriptions) | 11/17 (65 %) | 1.41 s |
| After (semantic router, ~10 tools) | **15/17 (88 %)** | **0.98 s** |

`qwen3.5:4b` was benchmarked and **rejected**: identical 88 % once shortlisted, but 13.7–17.2 s
median because 3.8 GB does not fit 4 GB of VRAM and 58 % runs on CPU.

### Overlay idle cost (20 s, idle/pill)

| | Before (4 windows) | After (1 window) |
| --- | --- | --- |
| Processes | 9 | 6 |
| CPU | **52.7 % of one core** | **1.4 % of one core** |
| Memory | 1128 MB | 739 MB |

### Not measured / unverified

* Hindi and Hinglish speech accuracy — the adopted STT models are English-only by construction.
* Wake-word false-accept rate (openWakeWord unchanged, not a measured problem).
* Multi-display placement — only one display is attached here.
* Peak VRAM during a full voice + vision turn.
* Suspend/resume reconnect behaviour.

---

## 3. Bugs found and fixed along the way

1. **Google tools were silently dropped.** `has_google` looked for `credentials.json` in the vault
   and `~/.credentials`, neither of which `--google-auth` writes. A fully linked account had all
   eight Calendar/Gmail/Tasks tools removed from the model's list.
2. **Battery was fabricated.** `system_stats` used `psutil.sensors_battery()`, which returns None
   on this laptop, so the model had no data and invented "85 %" — twice, confidently. Now falls
   back to `power_supply`, which reads `/sys` directly. Verified: 100 %, Full.
3. **Stale answers were replayed.** Restored history was presented as current, so that invented
   85 % came back verbatim on the next run instead of being re-measured. Restored turns are now
   fenced as stale.
4. **A question could trigger a write.** "What's on my calendar today" selected
   `google_calendar_create`. Write tools now rank below read tools for interrogative input.
5. **Nothing toggled with `el.hidden` was hiding.** A class `display: flex` outranks the UA
   `[hidden]` rule, so the panel claimed "Working…" while the pill sat idle.
6. **Task cards clipped their own content** — `overflow: hidden` on a grid item lets it shrink
   below its content; cards collapsed to 26 px.
7. **`dbus-monitor` leaked** one process per overlay launch when the overlay was killed rather
   than asked to quit.
8. **Two owners for the voice loop** — `overlay/main.js` spawned `--voice` unconditionally. It now
   checks whether systemd already runs it.
9. **Dead artefacts** — a `silero_vad.onnx` and a `tool-index-*.json` were on disk with nothing in
   the repo referencing either. Both capabilities are now real and wired.

---

## 4. What was built

| Area | Module | What it does |
| --- | --- | --- |
| Endpointing | `jarvis/audio/endpoint.py` | Silero streaming VAD, hysteresis, preroll, partial scheduling |
| STT | `jarvis/audio/local_stt.py` | committed vs partial transcripts, bounded-concurrency partials |
| TTS | `jarvis/audio/local_tts.py` | warm voice, sentence streaming, level callback |
| Levels | `jarvis/audio/levels.py` | tmpfs level publishing (the event path would drop audio frames) |
| Stop speech | `jarvis/audio/speech_control.py` | counter-based cross-process stop, distinct from cancel |
| Tool routing | `jarvis/agent/tool_router.py` | embedding shortlist, read/write bias, always-on set |
| Tool contract | `jarvis/agent/tool_contract.py` | validate/repair/refuse, outcome model |
| Context lens | `jarvis/integrations/lens.py` | selection / clipboard / window / screen capture |
| Memory control | `jarvis/memory/control.py` | search with provenance, correct, forget |
| Cancellation | `jarvis/jobs/cancel.py` | drop queued, signal running, report what would not stop |
| Overlay | `overlay/` | one window, three forms, real audio, persisted bounds |

Tests: **231 → 358**, all passing.

---

## 5. Next concrete action

Nothing is mid-flight. Optional follow-ups, in value order:

1. Move the Ollama model store to `~/Madara` (needs sudo; command in RESEARCH.md), then
   `ollama rm qwen3.5:4b` to reclaim 3.4 GB.
2. Evaluate Hindi/Hinglish with a multilingual Whisper model and measure the latency cost before
   changing the default.
3. `set_reminder` still receives `{text: ...}` without `when` from the small model. The contract
   layer catches it and the model self-corrects on retry; a clearer parameter name would avoid
   the round trip.
