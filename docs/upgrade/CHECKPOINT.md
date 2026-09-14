# JARVIS upgrade — checkpoint

Living document. Records what is done, what was decided and why, what was measured, and the next
concrete action, so a context reset never forces another full audit.

Branch: `worktree-fix-folder-move-paths`

---

## 0. Folder move repair — DONE (commit `191504d`, pushed)

`~/Dev` was moved to `~/Madara/Dev` (`~/Madara` is a separate 954 GB NVMe, `LinuxStorage`).
Everything storing an absolute path outside the repo broke.

Repaired:

| Thing | Was | Now |
| --- | --- | --- |
| 5 systemd user units | `/home/xor_sensei/Dev/Jarvis/...` | new path, enablement preserved |
| `~/.local/bin/jarvis` | dangling symlink | re-pointed |
| `~/.config/autostart/jarvis.desktop` | dead `start_jarvis.sh` | new path |
| `linkedin-copilot.service` | `%h/Dev/Linkdin/repo` | `%h/Madara/Dev/Linkdin/repo` |
| `viralyst-extractor.service` | `/home/.../Dev/Viralyst` | new path |
| `~/Madara/Dev/jarvis` | dangling symlink to old path | removed |
| **48 venv console scripts** | shebang `#!/home/xor_sensei/Dev/Jarvis/.venv/bin/python3` | new path |

The venv shebangs mattered most: `piper` is a console script, so **local TTS was failing with
ENOENT — Jarvis could not speak at all.** Fixed and verified (`piper --help` runs, synthesis works).

In-repo fixes: `start_jarvis.sh` now resolves its own location; `linkedin.py` finds the copilot as a
sibling checkout; new `scripts/relocate.sh` re-points installed integration at a given checkout
(rewrites only what is installed, never changes enable/disable state).

Still broken, unrelated to the move and **not fixed** (not Jarvis, needs owner input):
`tunnel-client.service` has `EnvironmentFile=/home/xor_sensei/.env`, which does not exist.

---

## 1. Environment (verified 2026-09-14)

| | |
| --- | --- |
| OS | Ubuntu 26.04 LTS, kernel 7.0.0-31-generic |
| Session | **Wayland**, GNOME (`ubuntu:GNOME`) |
| CPU | AMD Ryzen 5 7235HS — 4 cores / 8 threads |
| RAM | 22 GiB total, ~14 GiB available at rest |
| GPU | RTX 3050 Laptop, **4094 MiB VRAM**, ~569 MiB already used by the desktop |
| Root disk | `/` 167 G, **38 G free (77 % used)** — do not fill |
| Second NVMe | `~/Madara` 938 G, **926 G free**, ext4, writable — correct home for large downloads |
| Ollama models | `/usr/share/ollama/.ollama/models`, 3.7 G, **on the root disk** |
| Ollama daemon | `OLLAMA_KEEP_ALIVE=-1`, no `OLLAMA_MODELS` override |
| sudo | **no passwordless sudo** |

Already downloaded and reusable: faster-whisper `tiny.en` / `base.en` / `small.en` / `tiny`
(HF cache, 2.8 G), Piper `en_GB-alan-medium`, `silero_vad.onnx`, openWakeWord models,
Ollama `qwen2.5:3b`, `nomic-embed-text`, `moondream`.

**Blocked:** moving Ollama's model store to `~/Madara` needs a root systemd drop-in
(`OLLAMA_MODELS`), which needs sudo. A shell variable will not affect the running daemon.
Until then, Ollama pulls land on the 38 G root disk. User-level caches (HF, Piper, ONNX) can be
pointed at `~/Madara` without sudo.

---

## 2. Baseline measurements

Conditions: `jarvis-backend`, `jarvis-voice`, `jarvis-whatsapp`, `linkedin-copilot` all running;
VS Code and a browser open. Wall-clock, `time.perf_counter`, medians.

### Voice chain — end of speech to first spoken word

| Stage | Measured | n | Source |
| --- | --- | --- | --- |
| Silence wait before STT starts | **2.00 s** (fixed) | — | `JARVIS_SILENCE_MS` default 2000, RMS endpointing |
| STT `small.en` beam 5, CPU int8 | **1.70 s** median (RTF 0.46) | 9 | `faster_whisper` |
| Brain `qwen2.5:3b` (no tools) | 0.36 s – **2.24 s** | 3 each | Ollama `/api/chat` |
| TTS synth before *any* audio | **1.44 – 1.73 s** | 3 each | Piper synthesises the whole reply first |
| **Total** | **≈ 5.5 – 7.7 s** | | |

STT alternatives on the same clips (n=9 each):

| Model | beam | Load | Transcribe median | RTF |
| --- | --- | --- | --- | --- |
| `small.en` (current) | 5 | 1.4 s | 1.70 s | 0.46 |
| `small.en` | 1 | 0.9 s | 1.57 s | 0.43 |
| `base.en` | 1 | 0.6 s | **0.55 s** | 0.15 |
| `tiny.en` | 1 | 0.5 s | 0.30 s | 0.08 |

Not yet measured (marked unavailable until taken): overlay idle CPU / renderer RAM, startup time,
end-to-end tool-task latency, peak VRAM under load, wake-word latency and false-accept rate.

---

## 3. Findings from tracing the code

Real, verified issues — each is a concrete target:

1. **Endpointing is energy-only.** `jarvis/audio/vad.py` is pure RMS with a fixed
   `silence_ms` (2000 ms default). `silero_vad.onnx` is already downloaded to
   `~/.local/share/jarvis/` but **nothing in the repo references silero** — a dead download.
2. **STT is batch, CPU, `small.en`, beam 5.** No streaming, no partial transcript. The GPU is idle.
3. **TTS blocks on whole-reply synthesis.** `local_tts.synth()` runs Piper over the entire reply
   before the first sample plays, so long answers start with seconds of silence.
4. **Barge-in is a heuristic without AEC.** `_barge_in_monitor` samples 0.6 s of speaker echo, then
   needs ~0.6 s of sustained louder speech to cut in — slow, and it can mis-fire.
5. **Two owners for the voice loop.** `overlay/main.js` `ensureBackend()` spawns `--voice`
   unconditionally. `scripts/start.sh` sets `JARVIS_OVERLAY_SPAWN=0` so the normal path is safe, but
   `scripts/overlay.sh` does not — launching that directly gives two voice loops fighting for the mic.
6. **Overlay is four always-on-top windows** (ambient, console, spotify, cricket), not one
   interface that transforms. Window position/size are not persisted; `place()` re-centres on the
   primary display, ignoring multi-display and any drag the user made.
7. **Ambient layer obstructs real work.** In the baseline capture the SUBSYSTEMS panel sits on top
   of the VS Code file explorer and makes it unreadable.
8. **Invocation shortcut is hard-coded** (`Ctrl+Super+Space`, `Ctrl+Alt+J`, `Ctrl+Super+H`).
9. **Tests depend on the developer's `.env`.** `tests/test_brain_selection.py` fails without a real
   `GROQ_API_KEY`; with `GROQ_API_KEY=test-dummy` the suite is 231/231 green.

Working well, keep: XDG-portal screenshots (`jarvis/vision/screenshot.py`) — correct Wayland path
with fallbacks, verified working; narrow `preload.js` IPC surface; the systemd/service split.

Screenshots: `docs/upgrade/before/overlay-baseline.jpg`.

---

## 4. Next concrete action

Research phase (task #2): shortlist candidates for streaming STT, neural endpointing, streaming
TTS, a tool-calling small model, and overlay motion — then benchmark the finalists on this laptop.
