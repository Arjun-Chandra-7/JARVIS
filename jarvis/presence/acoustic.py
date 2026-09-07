"""Acoustic range-only presence: near-ultrasound FMCW with pulse compression.

This deliberately reports **range without bearing**. The measured microphone baseline
on this class of laptop is <= 0.7 cm (see scripts/probe-sensors.py), and lambda/2 at
19.75 kHz is 0.87 cm, so a single sample of timing jitter swamps the whole field of
view. Any azimuth from this array would be invented, so none is produced.

How it avoids the failure modes of a naive implementation:

* **Self-calibrating time origin.** Playback and capture latency are unknown and not a
  whole number of blocks, so de-chirping against "the chirp we just queued" mixes
  against the wrong reference. Instead every block is matched-filtered against the
  reference chirp and the *direct speaker-to-microphone path* — always the strongest,
  earliest peak — is used as t=0. All ranges are measured relative to it, so the
  latency cancels exactly and never needs to be known.
* **Pulse compression, not mix-and-lowpass.** Correlating with the reference chirp
  gives the full time-bandwidth product and a clean range profile.
* **A median clutter map instead of 3-pulse MTI.** MTI needs pulse-to-pulse phase
  coherence that a shared PipeWire stream cannot guarantee. A running median over
  recent profiles is the stationary room; subtracting it leaves people who move.
* **Guard interval.** The chirp is short relative to the period so an echo can never
  wrap into the next sweep.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

import numpy as np

from .types import Contact, SensorStatus

SR = 48000
F0, F1 = 18000.0, 21500.0          # near-ultrasound: inaudible to most adults, still passed by the hardware
CHIRP_S = 0.012                    # 12 ms sweep
PERIOD_S = 0.050                   # 50 ms between sweeps -> 38 ms of listening = 6.5 m unambiguous
CHIRP_N = int(SR * CHIRP_S)
PERIOD_N = int(SR * PERIOD_S)
C_AIR = 343.0

MIN_RANGE_M = 0.35                 # inside this the direct-path mainlobe dominates
MAX_RANGE_M = 4.0
CLUTTER_LEN = 40                   # ~2 s of profiles for the median clutter map
MIN_SNR_DB = 8.0                   # below this a peak is clutter residue, not a body
MAX_TARGETS = 3
PERSIST_FRAMES = 4                 # a real target holds still-ish across consecutive sweeps
PERSIST_HITS = 3
PERSIST_TOLERANCE_M = 0.30
AMPLITUDE = float(os.environ.get("JARVIS_SONAR_LEVEL", "0.12"))


def build_chirp() -> np.ndarray:
    """Hann-tapered linear FM sweep. The taper suppresses the click that would be audible."""
    t = np.linspace(0.0, CHIRP_S, CHIRP_N, endpoint=False)
    k = (F1 - F0) / CHIRP_S
    sweep = np.sin(2 * np.pi * (F0 * t + 0.5 * k * t * t))
    return (AMPLITUDE * np.hanning(CHIRP_N) * sweep).astype(np.float32)


def compress(rx: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Matched filter: correlate the echo with the transmitted sweep."""
    n = 1 << int(np.ceil(np.log2(len(rx) + len(reference))))
    spec = np.fft.rfft(rx, n) * np.conj(np.fft.rfft(reference, n))
    return np.abs(np.fft.irfft(spec, n))[:len(rx)]


def direct_path_index(profile: np.ndarray) -> Optional[int]:
    """Index of the speaker-to-mic leakage — the time origin for every range."""
    if profile.size == 0:
        return None
    idx = int(np.argmax(profile))
    # It must actually stand out, or we are looking at noise and have no timing reference.
    floor = float(np.median(profile)) + 1e-12
    return idx if profile[idx] > 8.0 * floor else None


def range_axis(n_bins: int, origin: int) -> np.ndarray:
    """Metres for each sample index, relative to the direct path. Round trip, hence /2."""
    return (np.arange(n_bins) - origin) * (C_AIR / SR) / 2.0


def cfar(profile: np.ndarray, guard: int = 4, train: int = 16,
         threshold_db: float = 16.0) -> list[tuple[int, float]]:
    """Cell-averaging CFAR. Returns (index, local_noise) so callers score SNR against
    the same noise estimate that authorised the detection.

    A bin must beat its local neighbourhood, not a global mean.

    The threshold is on magnitude, so it is deliberately high. Rayleigh-distributed
    noise magnitude routinely reaches ~4x its own mean, and with thousands of bins per
    sweep a 9 dB threshold produces dozens of false alarms every frame; 16 dB (a factor
    of ~6.3) keeps the false-alarm rate near zero while still catching real echoes.
    """
    n = len(profile)
    span = guard + train
    if n <= 2 * span + 1:
        return []
    ratio = 10 ** (threshold_db / 20.0)
    # Vectorised sliding sums: total window minus the guard band gives the training cells.
    csum = np.concatenate(([0.0], np.cumsum(profile, dtype=np.float64)))
    idx = np.arange(span, n - span)
    win = csum[idx + span + 1] - csum[idx - span]
    guard_sum = csum[idx + guard + 1] - csum[idx - guard]
    noise = (win - guard_sum) / (2 * train) + 1e-12
    strong = profile[idx] > ratio * noise
    local_max = (profile[idx] >= profile[idx - 1]) & (profile[idx] >= profile[idx + 1])
    keep = strong & local_max
    return [(int(i), float(n)) for i, n in zip(idx[keep], noise[keep])]


class AcousticSensor:
    """Emits sweeps and publishes range-only contacts for whatever is moving."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._contacts: list[Contact] = []
        self._status = SensorStatus("acoustic", False, "not started")
        self._seq = 0
        self._history: list[list[float]] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="presence-acoustic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            self._contacts, self._status = [], SensorStatus("acoustic", False, "stopped")

    def snapshot(self) -> tuple[list[Contact], SensorStatus]:
        with self._lock:
            return list(self._contacts), self._status

    def _set_status(self, ok: bool, detail: str) -> None:
        with self._lock:
            self._status = SensorStatus("acoustic", ok, detail)

    def _run(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            self._set_status(False, "sounddevice not installed")
            return

        chirp = build_chirp()
        tx = np.zeros(PERIOD_N, dtype=np.float32)
        tx[:CHIRP_N] = chirp                       # sweep then guard interval
        clutter: list[np.ndarray] = []

        # Playback/capture latency is unknown and may exceed a whole period, so we never
        # assume the chirp we queue lands in the block we read. The transmitter is a
        # free-running periodic loop and the analyser works on a two-period window, which
        # is guaranteed to contain one complete sweep plus its echoes wherever it fell.
        window_n = 2 * PERIOD_N
        ring = np.zeros(window_n, dtype=np.float64)
        phase = 0
        filled = 0
        lock = threading.Lock()

        def callback(indata, outdata, frames, _time, _status):
            nonlocal phase, ring, filled
            out = np.empty(frames, dtype=np.float32)
            for i in range(0, frames, PERIOD_N):           # cycle the periodic transmit buffer
                chunk = min(PERIOD_N, frames - i, PERIOD_N - phase)
                out[i:i + chunk] = tx[phase:phase + chunk]
                phase = (phase + chunk) % PERIOD_N
            outdata[:, 0] = out
            samples = np.asarray(indata[:, 0], dtype=np.float64)
            with lock:
                ring = np.roll(ring, -len(samples))
                ring[-len(samples):] = samples
                filled = min(window_n, filled + len(samples))

        try:
            stream = sd.Stream(samplerate=SR, blocksize=PERIOD_N, channels=(1, 1),
                               dtype="float32", callback=callback)
        except Exception as exc:  # noqa: BLE001
            self._set_status(False, f"audio stream unavailable: {exc}")
            return

        resolution_cm = C_AIR / (2 * (F1 - F0)) * 100
        with stream:
            self._set_status(True, f"{F0/1000:.0f}-{F1/1000:.1f} kHz, {resolution_cm:.1f} cm bins, range only")
            while not self._stop.is_set():
                time.sleep(PERIOD_S * 2)
                with lock:
                    if filled < window_n:
                        continue
                    frame = ring.copy()

                profile = compress(frame, chirp)
                origin = direct_path_index(profile)
                if origin is None:
                    self._set_status(False, "no direct path — speaker muted or volume too low")
                    self._publish([])
                    continue

                clutter.append(profile)
                if len(clutter) > CLUTTER_LEN:
                    clutter.pop(0)
                if len(clutter) < 8:
                    continue
                # The stationary room is the running median; what is left over moved.
                residual = np.clip(profile - np.median(np.array(clutter), axis=0), 0, None)
                self._publish(self._targets(residual, origin))

    def _targets(self, residual: np.ndarray, origin: int) -> list[Contact]:
        ranges = range_axis(len(residual), origin)
        lo = int(np.searchsorted(ranges, MIN_RANGE_M))
        hi = int(np.searchsorted(ranges, MAX_RANGE_M))
        if hi <= lo:
            return []
        window = residual[lo:hi]
        now = time.time()

        # Score against the CFAR training cells, not a global median. The residual is
        # half zeros after clipping, so a global median is ~0 and turns every peak into
        # a nonsensical 200 dB.
        raw: list[tuple[float, float]] = []
        for peak, noise in cfar(window):
            idx = lo + peak
            snr_db = float(min(40.0, 20 * np.log10(max(residual[idx], 1e-12) / max(noise, 1e-9))))
            if snr_db >= MIN_SNR_DB:
                raw.append((float(ranges[idx]), snr_db))

        # A person persists; multipath and room ring flicker. Only report a range that
        # has been seen in at least PERSIST_HITS of the last PERSIST_FRAMES sweeps.
        self._history.append([r for r, _ in raw])
        if len(self._history) > PERSIST_FRAMES:
            self._history.pop(0)

        out: list[Contact] = []
        for distance, snr_db in sorted(raw, key=lambda item: -item[1]):
            hits = sum(1 for frame in self._history
                       if any(abs(prev - distance) <= PERSIST_TOLERANCE_M for prev in frame))
            if hits < PERSIST_HITS:
                continue
            if any(abs((c.distance_m or 0) - distance) <= PERSIST_TOLERANCE_M for c in out):
                continue                       # one contact per resolvable range cell
            self._seq += 1
            out.append(Contact(
                id=f"ac-{self._seq}",
                source="acoustic",
                distance_m=distance,
                bearing_deg=None,               # genuinely unknown on this hardware
                confidence=round(min(0.7, 0.2 + snr_db / 60.0), 3),
                moving=True,                    # the clutter map removes everything static
                first_seen=now, last_seen=now,
                detail=f"moving echo, {snr_db:.0f} dB over clutter",
            ))
            if len(out) >= MAX_TARGETS:
                break
        return sorted(out, key=lambda c: c.distance_m or 0)

    def _publish(self, contacts: list[Contact]) -> None:
        with self._lock:
            self._contacts = contacts


_sensor: Optional[AcousticSensor] = None


def sensor() -> AcousticSensor:
    global _sensor
    if _sensor is None:
        _sensor = AcousticSensor()
    return _sensor
