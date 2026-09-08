# Detecting people with only a laptop — what actually works

Researched and measured on this machine (MediaTek MT7921 Wi-Fi, Bison integrated webcam,
stereo mics ~0.7 cm apart, one speaker). The question was: with **no extra hardware**,
what is the best way to detect people? Here is every serious option, ranked, with why.

## 1. Camera + full-body person detection — THE answer  ✅ built

A modern CNN person detector (YOLOX, run on CPU via onnxruntime) finds a whole human —
head, torso, or legs — not just a face. This is the state of the art for device-free
people sensing on a commodity laptop, and it is what we now run.

- **Gives:** presence, head count, horizontal bearing (±2°), and metric distance when a
  face is visible (interpupillary distance is a 63 ± 3 mm constant → range with no
  calibration). Validated: 4/4 people on a reference image at 0.91 confidence.
- **Robust to** turned heads, side profiles, poor light, and a body half-out of frame —
  exactly the cases where the old face-only detector failed.
- **Costs:** needs the lens pointed at the person and some light. On a laptop that is a
  given when you are sitting at it — provided the lid is tilted so the camera sees you,
  not the ceiling.
- Why not heavier models (YOLOv8, RT-DETR)? YOLOX-S already runs comfortably at the 2–4
  fps a presence sensor needs, and is Apache-licensed from the same repo as our face
  model. No accuracy gain would justify the extra weight for this use.

## 2. Wi-Fi device presence via monitor mode — the best *camera-free* option  ◑ feasible

Every phone and watch emits 802.11 frames. In **monitor mode** the Wi-Fi card hears the
MAC addresses of nearby devices without joining anything, so counting distinct,
non-randomised MACs is a real proxy for how many people are around — through walls, in
the dark, with the lid shut.

- **This laptop's MT7921 supports monitor mode** (confirmed with `iw list`).
- **Costs:** needs root, and putting the card in monitor mode drops your Wi-Fi
  connection (unless a second adapter is used). Modern phones randomise their MAC until
  associated, which blunts counting. So it is a genuine but coarse, opt-in signal —
  worth adding as a manual "count devices around me" scan, not an always-on sensor.
- This is the honest version of "detect people by their devices". It is *not* the same
  as reading anyone's traffic or password (see the Wi-Fi tool, which only reveals
  passwords this machine itself has saved).

## 3. Wi-Fi CSI (through-wall pose / breathing) — not on this chip  ✗

The research frontier (CMU's *DensePose-from-WiFi*, breathing/heart-rate from CSI) reads
the **Channel State Information** — per-subcarrier amplitude/phase — and infers bodies
through walls. It is astonishing and genuinely hardware-free of cameras.

- **But CSI extraction needs specific chips/firmware**: Intel 5300 (iwl-csi), Atheros
  ath9k (Atheros-CSI-Tool), or Broadcom via nexmon; on ESP32 it is trivial.
- **The MT7921 in this laptop exposes no CSI interface.** There is no maintained tool to
  pull CSI from it. So through-wall Wi-Fi sensing is not available here — a hardware
  limit, full stop.

## 4. Passive acoustic (listen for a voice) — works as presence  ✅ built

Listen-only, no sound emitted: is there speech in the room? Separates a voice from fans
and keyboards by band energy, band dominance, and syllable-rate modulation. Measured
+17 dB separation between a quiet room and talking; detected talking 6/6. No direction
(the mic baseline is far too short), but a reliable "someone is here, talking" with the
lid shut and the lights off.

## 5. Active acoustic sonar (chirp + echo) — does not work here  ✗ built, off by default

Emitting an 18–21.5 kHz FMCW chirp and ranging the echoes is sound in theory, but
measured live it cannot separate a moving person from room reverberation — laptop
speakers are weak at ultrasound, a body reflects it poorly, and the direct speaker→mic
path is 60–80 dB louder than any echo. Kept in the tree, off, as a motion hint only.

## 6. What is impossible on this hardware, and why

- **Bearing from the microphones.** The two mics are ≤ 0.7 cm apart; λ/2 at 19.75 kHz is
  0.87 cm, so one sample of timing jitter is > 40° of error. No processing fixes a
  baseline that short. Direction comes only from the camera.

## Verdict

For "detect people without anything else", the honest ranking on this laptop is:

1. **Camera + YOLOX person detection** — accurate, gives direction and count. *Point the
   lid at yourself and it works.*  ← now built and running
2. **Passive audio** — reliable presence with no camera, no light, no line of sight.  ← built
3. **Wi-Fi monitor-mode device counting** — coarse, camera-free, through walls; needs
   root and drops your connection.  ← feasible, not yet built (opt-in tool if wanted)
4. **Named devices** (paired MACs) — identity, no position.  ← built

Everything better than this (CSI through-wall) needs a chip this laptop does not have.
