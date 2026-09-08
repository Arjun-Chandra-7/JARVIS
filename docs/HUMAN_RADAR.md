# Human radar — what a laptop can and cannot sense

> Technology comparison and the reasoning behind these choices: **docs/PEOPLE_DETECTION_RESEARCH.md**.

Measured on this machine with `scripts/probe-sensors.py`. Every design choice below
follows from these numbers, not from wishful thinking.

## Hardware reality

| Measurement | Value | Consequence |
|---|---|---|
| L/R coherence, 100–500 Hz | **0.92** | two genuine microphones, same acoustic field |
| GCC-PHAT lag spread | **−1..0 samples** @48 kHz | effective baseline **≤ 0.7 cm** |
| λ/2 at 19.75 kHz | 0.87 cm | phase DOA is *just* unambiguous, but… |
| …bearing error per sample of jitter | **> 40°** | **acoustic azimuth is not achievable** |
| 18–21.5 kHz transmit + receive | works, well above noise floor | **acoustic range-only IS achievable** |
| Camera | Integrated, 1280×720, MJPG/YUYV | **bearing + range + identity** |

### The bug this replaces
The previous `sonar.py` assumed `_MIC_DISTANCE = 0.18` m. The real baseline is
≤ 0.007 m — wrong by ~25×. Every azimuth it produced was noise, which is why it
always reported one or two targets at ≈0°. Three further defects made the range
meaningless too: the transmitted chirp was never time-aligned with the received
block (so de-chirping used the wrong reference), the 3-pulse MTI canceller assumed
pulse-to-pulse phase coherence that the unsynchronised stream never had, and the
band-pass `sosfilt` was restarted every block, injecting an edge transient that
CFAR then detected as a near-range target.

## Design that follows from the measurements

One fused track list, each track carrying **provenance and confidence** — never a
fabricated position.

| Source | Gives | Range | Limits | Verdict |
|---|---|---|---|---|
| **Passive audio** | "someone is here, talking" | room | no bearing, no range | **works** — measured +17 dB separation between a quiet room and speech |
| **BT/Wi-Fi** | *identity* + room presence | whole home | no position; device-bound | **works** |
| **Camera + YOLOX body** | presence, bearing ±2°, count; distance when a face shows | ~0.4–6 m, within FOV | needs light + the lens aimed at you | **works — full-body, robust to turned heads/poor light** |
| **Acoustic FMCW** | distance + motion, in theory | ~0.3–4 m | no bearing | **does not work** — see below |

### The acoustic channel does not detect people on this hardware

Measured live: "stay still" and "wave your hand" produce statistically indistinguishable
output — random contacts scattered between 0.6 m and 3.8 m in both cases. It is picking
up room reverberation, not bodies. The link budget is the reason: a laptop speaker is
heavily attenuated at 18–21.5 kHz, a human body is a poor reflector there, and the
speaker-to-mic direct path is 60–80 dB stronger than any echo.

It is left in the tree, off by default, as a motion hint only. It must not be presented
as people-detection.

Distance from the camera is anchored on interpupillary distance: human IPD is
63 ± 3 mm across adults, so `distance = f_px · 0.063 / ipd_px` is metric without
any per-user calibration. `f_px` comes from the measured horizontal FOV.

A contact seen only by the acoustic sensor is rendered as a **range arc**, not a
point — because its bearing is genuinely unknown. That honesty is the whole point.

## Using it

```bash
# live readout (reads the running backend if there is one)
.venv/bin/python scripts/radar-check.py

# measure your own webcam instead of assuming a 68 deg FOV
.venv/bin/python scripts/radar-check.py --calibrate 1.5   # stand 1.5 m away

# what the hardware can sense at all
.venv/bin/python scripts/probe-sensors.py
```

Ask by voice: **"Jarvis, who's around?"** / "is anyone here" / "scan the room".
Bind a device to a person once: *"remember that A0:11:22:33:44:55 is Maya's"*.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `JARVIS_PRESENCE` | `audio,network` | which sensors run; add `camera` for full-body detection + bearing, or `off` |
| `JARVIS_PERSON_DETECT` | `1` | `0` uses face-only (skips the YOLOX body model) |
| `JARVIS_CAM_HFOV_DEG` | `68` | camera horizontal FOV — set from `--calibrate` |
| `JARVIS_PRESENCE_FPS` | `4` | camera detection rate |
| `JARVIS_CAMERA_INDEX` | auto | pin a `/dev/videoN`; otherwise auto-discovered |
| `JARVIS_OVERLAY_CAMERA` | `0` | `1` gives the webcam to the overlay instead of the radar |

`acoustic` is off by default: it holds the speaker continuously, which fights
text-to-speech and is audible to some people and most pets.

### Camera ownership

Only one process can hold the webcam. The backend presence service owns it so the radar
works headless; the overlay no longer grabs it. The device nodes renumber whenever the
camera re-enumerates (observed here: `video0/1` → `video1/2` → `video0/2`), so the node
is discovered by testing which one actually delivers a frame, not assumed.

### Working without a camera

The default (`audio,network`) needs no webcam, no light and no line of sight. What you
get is **presence and identity, not position**:

- "I can hear someone talking, but I can't tell where from"
- "Maya nearby by device"

Adding `camera` is the only way to get a bearing or a distance. That is a hardware fact,
not a limitation of the code.

### What it will not do

- No bearing from audio. Not a tuning choice — the baseline is 25x too short.
- No detection of a motionless person outside the camera's field of view. The clutter
  map removes anything that does not move, which is what makes it robust indoors.
- No through-wall sensing. Wi-Fi CSI would allow it, but this laptop's chipset does not
  expose CSI, so the only through-wall signal is a bound device's presence.
