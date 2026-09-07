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
