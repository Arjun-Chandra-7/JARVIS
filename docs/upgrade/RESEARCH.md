# Research and adoption record

What was considered, what was measured **on this laptop** (Ryzen 5 7235HS, RTX 3050 4 GB,
22 GiB RAM, Ubuntu 26.04, Wayland/GNOME), and what was adopted, deferred or rejected.

Upstream claims and on-device measurements are kept separate throughout. Anything not actually
run here is labelled **not measured**.

---

## Decision summary

| # | Candidate | Capability | Verdict | Why |
| --- | --- | --- | --- | --- |
| 1 | Silero VAD v5 (via `faster-whisper`) | neural endpointing | **Adopt** | 2000 ms → ~90 ms after end of speech, 0.087 ms/window. Already a dependency. |
| 2 | `faster-whisper` `base.en` | committed transcript | **Adopt** | 1.70 s → 0.55 s vs `small.en` beam 5, no errors on the command set. Already downloaded. |
| 3 | `faster-whisper` `tiny.en` | live partial transcript | **Adopt** | ~440 ms/update, disposable, runs off the capture thread. Already downloaded. |
| 4 | Piper Python API (`PiperVoice`) | streaming TTS | **Adopt** | First audio 1440 ms → 155 ms by keeping the voice loaded. No new dependency. |
| 5 | `nomic-embed-text` (Ollama) | tool routing | **Adopt** | Router recall 15/15 at keep=4; 44 ms/route. Already pulled for vault recall. |
| 6 | `qwen2.5:3b` (Ollama) | brain — keep | **Keep** | 0.98 s median with routing, fits VRAM. |
| 7 | `qwen3.5:4b` (Ollama) | brain — replace | **Reject** | Same accuracy as the 3B once both get a shortlist, but 13.7–17.2 s median: 3.8 GB does not fit 4 GB VRAM, 58 % runs on CPU. |
| 8 | `qwen3.5:2b` | brain — replace | **Defer** | Would fit VRAM, but #6 already reaches 88 %; the limit is argument shape, not model size. Not measured. |
| 9 | Kokoro-82M (ONNX) | TTS quality | **Defer** | RTF 0.45–0.51 upstream; Piper already measures RTF 0.05–0.07 here. Would be a voice-quality change, not a latency one. |
| 10 | sherpa-onnx streaming ASR | true streaming partials | **Defer** | Real streaming, but 632 MB and a new runtime; #3 gives a live transcript from a model already on disk. |
| 11 | NVIDIA Nemotron streaming (via sherpa-onnx) | streaming ASR | **Defer** | 650 MB INT8; same reasoning as #10, and root disk has only 38 GB free. |
| 12 | openWakeWord | wake word — keep | **Keep** | Already integrated and not a measured problem. |
| 13 | microWakeWord | wake word | **Reject** | Targets microcontrollers; no benefit on a laptop. |
| 14 | Picovoice Porcupine | wake word | **Reject** | Better FA/FR upstream, but needs an account key. #12 is keyless and adequate. |
| 15 | `wl-paste --primary` | selected-text capture | **Adopt** | Already installed; captures the selection with no new permission. |
| 16 | XDG Desktop Portal screenshot | screen capture | **Keep** | Already the implementation, and the correct Wayland path. Verified working. |
| 17 | GNOME Shell `Eval` | active window title | **Reject** | Blocked on modern GNOME. Reports its own unavailability rather than pretending. |
| 18 | Tesseract / OCR | screen text | **Reject** | The existing Gemini/moondream vision path already returns screen text. |
| 19 | `motion` / GSAP | overlay animation | **Reject** | CSS transitions plus one `requestAnimationFrame` loop cover every need; a library would add weight to a window that must idle at ~0 %. |
| 20 | Chrome DevTools Protocol | UI verification | **Adopt (dev only)** | Drives the real Electron UI for verification. Not shipped in the app. |

---

## Measured on this laptop

### Speech to text (n=9 per row, background services running)

Clips synthesised with the shipped Piper voice; wall clock, medians.

| Model | Beam | Load | Transcribe | RTF |
| --- | --- | --- | --- | --- |
| `small.en` (was default) | 5 | 1.4 s | 1.70 s | 0.46 |
| `small.en` | 1 | 0.9 s | 1.57 s | 0.43 |
| **`base.en` (now default)** | 1 | 0.6 s | **0.55 s** | 0.15 |
| `tiny.en` (partials only) | 1 | 0.5 s | 0.30 s | 0.08 |

### Endpointing

| | Before | After |
| --- | --- | --- |
| Mechanism | RMS energy + fixed timeout | Silero VAD v5, hysteresis + hangover |
| Wait after speech ends | 2000 ms | **+89 ms** (300 ms hangover) |
| Cost | negligible | 0.087 ms per 32 ms window (~0.27 % of one core) |

Silero's input convention is the trap: each step needs the 64 preceding samples in front of the
512 new ones. Feeding bare 512-sample windows returns ~0 for obvious speech — that is what made
the stray `silero_vad.onnx` in `~/.local/share/jarvis` look broken. `tests/test_endpoint.py` pins
it down.

### Text to speech (n=3 per row)

| Reply length | Before (CLI per reply) | After (voice kept loaded, per sentence) |
| --- | --- | --- |
| 42 chars → 3.1 s audio | 1440 ms to first audio | **155 ms** |
| 141 chars → 9.4 s audio | 1730 ms | **191 ms** |
| 163 chars → 10.3 s audio | not measured | **150 ms** |

Piper's real cost was process startup (~1.8 s to load the voice), paid on every single utterance.
Warm RTF is 0.05–0.07, so synthesis itself was never the problem.

### Tool calling — 17 representative Jarvis commands

Scored on tool choice **and** argument validity against the registered schema.

| Variant | Model | Tools shown | Correct | Median |
| --- | --- | --- | --- | --- |
| Shipped | `qwen2.5:3b` | 84, clipped descriptions | 11/17 (65 %) | 1.41 s |
| + full descriptions | `qwen2.5:3b` | 84 | 10/17 (59 %) | 1.48 s |
| Bigger model | `qwen3.5:4b` | 84 | 5/17 (29 %) | 17.15 s |
| Bigger model + shortlist | `qwen3.5:4b` | ~18 | 15/17 (88 %) | 13.73 s |
| **Shipped now** | **`qwen2.5:3b`** | **~10, routed** | **15/17 (88 %)** | **0.98 s** |

Two findings worth stating plainly:

* **Tool count dominates, not model size.** The 4B model went from 29 % to 88 % purely by seeing
  fewer choices. Adding full descriptions to all 84 tools made things slightly *worse*, which is
  the opposite of what I expected before measuring.
* **A model fitting on disk is not the same as running it comfortably.** `qwen3.5:4b` is 3.8 GB
  against 4 GB of VRAM shared with the desktop, so Ollama splits it 58 % CPU / 42 % GPU and a turn
  takes 14–17 s. Unusable for voice, at identical accuracy.

Router recall (is the right tool even offered?) is 15/15 at keep=4, 6, 8 and 10, at ~44 ms per
route. Routing is not what limits accuracy any more — the residual failures are argument shape,
which `tool_contract` catches at runtime and returns to the model as a correction. The benchmark
harness scores a single turn with no retry, so it counts those as failures even though the live
agent self-corrects (observed: a bad `google_calendar_create` call was rejected and re-issued as
the correct `google_agenda`).

### Overlay idle cost (20 s window, pill / idle state)

| | Before (4 windows) | After (1 window) |
| --- | --- | --- |
| Electron processes | 9 | 6 |
| CPU | **52.7 % of one core** (6.6 % of 8) | **1.4 % of one core** (0.18 % of 8) |
| Resident memory | 1128 MB | 739 MB |

The old overlay never stopped animating: the ambient radar sweep, the waveform, the Spotify and
cricket widgets all ran `requestAnimationFrame` loops whether or not anything was happening. The
new one runs no animation loop at idle and none at all while hidden.

---

## Licences

| Component | Code licence | Weights / voice licence |
| --- | --- | --- |
| faster-whisper | MIT | Whisper models MIT (OpenAI) |
| Silero VAD | MIT | MIT |
| Piper | MIT | voice `en_GB-alan-medium`, MIT-licensed VITS weights |
| openWakeWord | Apache-2.0 | Apache-2.0 |
| Qwen2.5-3B | Apache-2.0 | Apache-2.0 |
| nomic-embed-text | Apache-2.0 | Apache-2.0 |

All are already vendored in the existing environment; this work added no new package and no new
download.

## Languages

Everything adopted for speech is **English-only by construction**: `base.en` and `tiny.en` are
English models, and the Piper voice is `en_GB`. Hindi and Hinglish were **not** evaluated, because
switching to a multilingual Whisper model is a separate measured trade-off (the multilingual
models are larger and slower, and the current 0.55 s budget is what makes the voice loop feel
immediate). `JARVIS_STT_LANGUAGE=auto` with a multilingual model remains the path; treat it as
**unverified** until someone measures WER on real Hinglish speech.

## Storage

Large downloads should land on `~/Madara` (938 GB, 926 GB free) rather than `/` (38 GB free).
User-level caches can be pointed there without privileges. Ollama's model store is at
`/usr/share/ollama/.ollama/models` on the root disk and moving it needs a root systemd drop-in:

```bash
sudo systemctl edit ollama            # add:  [Service]
                                      #       Environment="OLLAMA_MODELS=/home/xor_sensei/Madara/ollama-models"
sudo rsync -a /usr/share/ollama/.ollama/models/ /home/xor_sensei/Madara/ollama-models/
sudo chown -R ollama:ollama /home/xor_sensei/Madara/ollama-models
sudo systemctl restart ollama
```

This session had no passwordless sudo, so that was **not done**. One model (`qwen3.5:4b`, 3.4 GB)
was pulled for the benchmark and is no longer needed — `ollama rm qwen3.5:4b` reclaims it.
