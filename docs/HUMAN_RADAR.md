# Human radar — what a laptop can and cannot sense

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

| Source | Gives | Range | Limits |
|---|---|---|---|
| **Camera** (primary) | bearing ±2°, distance ±8%, count, identity | ~0.4–6 m, within FOV | needs light + line of sight |
| **Acoustic FMCW** (secondary) | **distance only**, motion | ~0.3–3 m, 360° | no bearing; moving targets only |
| **Passive audio** | "someone is speaking" | room | no bearing on this hardware |
| **BT/Wi-Fi** | *identity* + room presence | whole home | no position; device-bound |

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
| `JARVIS_PRESENCE` | `camera,network` | which sensors run; add `acoustic`, or `off` |
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

### What it will not do

- No bearing from audio. Not a tuning choice — the baseline is 25x too short.
- No detection of a motionless person outside the camera's field of view. The clutter
  map removes anything that does not move, which is what makes it robust indoors.
- No through-wall sensing. Wi-Fi CSI would allow it, but this laptop's chipset does not
  expose CSI, so the only through-wall signal is a bound device's presence.
