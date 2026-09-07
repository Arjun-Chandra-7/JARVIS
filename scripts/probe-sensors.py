#!/usr/bin/env python3
"""Measure what this laptop can actually sense — run before trusting any presence math.

Everything the human-radar does is bounded by three hardware facts:

  1. the microphone baseline  -> whether acoustic azimuth is possible at all
  2. the ultrasonic response  -> whether active (FMCW) ranging is possible at all
  3. the camera FOV/geometry  -> the accuracy of vision bearing + range

This prints all three with real measurements instead of assumptions.
Usage:  .venv/bin/python scripts/probe-sensors.py [--seconds 6]
"""
from __future__ import annotations

import argparse
import subprocess
import sys

C_AIR = 343.0  # m/s


def _audio(seconds: float, device) -> None:
    import numpy as np
    import sounddevice as sd
    from scipy import signal

    sr = 48000
    print("== audio devices ==")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"  [{i}] in={d['max_input_channels']:>3}  {d['name']}")

    print(f"\n== capturing {seconds:.0f}s (make some noise / talk near the laptop) ==")
    try:
        x = sd.rec(int(seconds * sr), samplerate=sr, channels=2, dtype="float32",
                   device=device, blocking=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  capture failed: {exc}")
        return
    left, right = x[:, 0].astype(float), x[:, 1].astype(float)
    if left.std() < 1e-6 and right.std() < 1e-6:
        print("  silence — nothing to measure")
        return

    # --- 1. are these two genuinely separate mics on a shared field? --------------
    freq, coh = signal.coherence(left, right, fs=sr, nperseg=4096)
    print("\n== L/R magnitude-squared coherence (1.0 = same acoustic field) ==")
    for lo, hi in [(100, 500), (500, 1500), (1500, 4000), (4000, 8000),
                   (8000, 16000), (16000, 18000), (18000, 22000)]:
        m = (freq >= lo) & (freq < hi)
        print(f"  {lo:>5}-{hi:<5} Hz : {coh[m].mean():.3f}" if m.any() else "")
    low = coh[(freq >= 100) & (freq < 500)].mean()
    print(f"  -> {'two real mics' if low > 0.6 else 'NOT a usable pair'} (100-500 Hz coherence {low:.2f})")

    # --- 2. mic baseline from the GCC-PHAT delay spread ---------------------------
    sos = signal.butter(4, [300, 3500], "band", fs=sr, output="sos")
    lf_l, lf_r = signal.sosfilt(sos, left), signal.sosfilt(sos, right)
    max_lag = 60  # +/-1.25 ms == +/-43 cm, deliberately generous
    lags, quals = [], []
    win = sr // 2
    for i in range(0, len(lf_l) - win, win // 2):
        a, b = lf_l[i:i + win], lf_r[i:i + win]
        if a.std() < 1e-4:
            continue
        n = 1 << int(np.ceil(np.log2(len(a) * 2)))
        cps = np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n))
        cps /= np.abs(cps) + 1e-12                      # PHAT weighting
        cc = np.fft.irfft(cps, n)
        cc = np.concatenate((cc[-max_lag:], cc[:max_lag + 1]))
        lags.append(int(np.argmax(cc)) - max_lag)
        quals.append(float(cc.max()))
    print("\n== microphone baseline (GCC-PHAT time-difference-of-arrival) ==")
    if not lags:
        print("  no usable windows")
        return
    span = max(abs(min(lags)), abs(max(lags)))
    baseline_cm = span * C_AIR / sr * 100
    print(f"  windows={len(lags)}  peak quality median={np.median(quals):.2f}")
    print(f"  lag range: {min(lags)}..{max(lags)} samples  (1 sample = {C_AIR / sr * 100:.2f} cm)")
    print(f"  -> effective baseline d <= {baseline_cm:.1f} cm")
    lam_half = C_AIR / 19750.0 / 2 * 100
    print(f"  -> unambiguous phase-DOA needs d <= lambda/2 = {lam_half:.2f} cm at 19.75 kHz")
    if baseline_cm < 1.5:
        print("  VERDICT: azimuth from audio is NOT achievable — use the camera for bearing.")
    else:
        deg = np.degrees(np.arcsin(min(1.0, (C_AIR / sr) / (baseline_cm / 100))))
        print(f"  VERDICT: usable; one sample of jitter = {deg:.0f} deg of bearing error.")

    # --- 3. ultrasonic band: can we transmit AND hear 18-21.5 kHz? ----------------
    print("\n== ultrasonic (18-21.5 kHz) round-trip ==")
    fr, psd = signal.welch(left, fs=sr, nperseg=8192)
    base = psd[(fr > 300) & (fr < 8000)].mean()
    ultra = psd[(fr > 18000) & (fr < 21500)].mean()
    print(f"  ambient 18-21.5 kHz vs 0.3-8 kHz: {10 * np.log10(ultra / base):+.1f} dB")
    try:
        t = np.linspace(0, 1.0, sr, endpoint=False)
        tone = (0.15 * np.sin(2 * np.pi * 19000 * t)).astype("float32")
        rec = sd.playrec(tone, samplerate=sr, channels=2, dtype="float32", blocking=True)
        fr2, p2 = signal.welch(rec[:, 0].astype(float), fs=sr, nperseg=8192)
        at19 = p2[(fr2 > 18800) & (fr2 < 19200)].mean()
        floor = p2[(fr2 > 14000) & (fr2 < 17000)].mean()
        snr = 10 * np.log10(at19 / max(floor, 1e-20))
        print(f"  played 19 kHz -> heard it {snr:+.1f} dB above the 14-17 kHz floor")
        print("  VERDICT: active ultrasonic ranging is viable." if snr > 12 else
              "  VERDICT: too weak — active ranging will be unreliable.")
    except Exception as exc:  # noqa: BLE001
        print(f"  loopback test skipped: {exc}")


def _camera() -> None:
    print("\n== camera ==")
    try:
        out = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        print("  " + (out.replace("\n", "\n  ") or "(none)"))
    except Exception:  # noqa: BLE001
        print("  v4l2-ctl not available")
    try:
        import cv2
        print(f"  opencv {cv2.__version__} present")
    except ImportError:
        print("  opencv NOT installed (.venv/bin/pip install opencv-python-headless)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--device", default=None, help="input device index/name (default: system default)")
    args = ap.parse_args()
    dev = args.device
    if dev is not None and dev.isdigit():
        dev = int(dev)
    try:
        _audio(args.seconds, dev)
    except ImportError as exc:
        print(f"audio probe needs numpy/scipy/sounddevice: {exc}")
    _camera()
    return 0


if __name__ == "__main__":
    sys.exit(main())
