"""Passive audio presence: is anyone actually here, talking?

No sound is emitted and no direction is claimed. The microphone baseline on this class
of laptop is <= 0.7 cm, so bearing is impossible (see scripts/probe-sensors.py); what is
very reliable is *whether a human voice is present in the room*.

Speech is separated from fans, keyboards and traffic by three cheap features computed on
the same frame:

* **band energy** in 300-3400 Hz, the telephony band that carries nearly all speech power
* **speech-band dominance** — that band's share of total energy, which rejects broadband
  noise (fans, hiss) and low rumble even when they are loud
* **flux variability** — speech is modulated at a syllable rate of roughly 2-8 Hz, so its
  frame-to-frame energy varies a lot, whereas a fan is almost perfectly steady

The noise floor is learned continuously from the quietest recent frames, so the sensor
adapts to a room rather than relying on a fixed threshold.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

import numpy as np

from .types import Contact, SensorStatus

SR = 16000
FRAME_S = 0.064
FRAME_N = int(SR * FRAME_S)
SPEECH_LO, SPEECH_HI = 300.0, 3400.0

FLOOR_LEN = 120                    # ~8 s of frames to learn the room's quiet level
HISTORY_LEN = 16                   # ~1 s, long enough to see syllable-rate modulation
SNR_DB = float(os.environ.get("JARVIS_PASSIVE_SNR_DB", "9.0"))
# 300-3400 Hz is 39% of the 0-8000 Hz spectrum, so *flat* noise already scores ~0.39.
# The threshold must sit clearly above that baseline or it rejects nothing; speech
# typically concentrates 70-90% of its energy in this band.
MIN_DOMINANCE = 0.55
MIN_FLUX = 0.25                    # relative std of recent band energy
HOLD_S = 3.0                       # keep reporting presence this long after the last speech
# One frame is not a conversation. A cough, a keystroke or a chair creak can pass the
# per-frame test, so require several speech frames inside a short window before saying
# somebody is here. Without this the sensor latches on the first blip and never clears.
VOTE_WINDOW = 10                   # frames (~0.6 s)
VOTE_HITS = 4


def features(frame: np.ndarray) -> tuple[float, float]:
    """(speech-band energy, that band's share of total energy) for one frame."""
    if frame.size == 0:
        return 0.0, 0.0
    windowed = frame.astype(np.float64) * np.hanning(len(frame))
    spectrum = np.abs(np.fft.rfft(windowed)) ** 2
    freqs = np.fft.rfftfreq(len(frame), 1.0 / SR)
    band = spectrum[(freqs >= SPEECH_LO) & (freqs <= SPEECH_HI)].sum()
    total = spectrum.sum() + 1e-20
    return float(band), float(band / total)


def is_speech(energies: list[float], dominance: float, floor: float) -> bool:
    """Speech = loud enough for this room, concentrated in band, and syllable-modulated."""
    if len(energies) < 4:
        return False
    current = energies[-1]
    if current <= floor * (10 ** (SNR_DB / 10.0)):
        return False
    if dominance < MIN_DOMINANCE:
        return False                       # broadband noise, not voice
    recent = np.array(energies[-HISTORY_LEN:], dtype=float)
    flux = float(recent.std() / (recent.mean() + 1e-20))
    return flux >= MIN_FLUX                # a steady fan has almost no flux


class PassiveSensor:
    """Listens without transmitting and reports 'someone is here' when speech is heard."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._contacts: list[Contact] = []
        self._status = SensorStatus("audio", False, "not started")
        self._last_speech = 0.0
        self._first_speech = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="presence-passive", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            self._contacts, self._status = [], SensorStatus("audio", False, "stopped")

    def snapshot(self) -> tuple[list[Contact], SensorStatus]:
        with self._lock:
            return list(self._contacts), self._status

    def _run(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            with self._lock:
                self._status = SensorStatus("audio", False, "sounddevice not installed")
            return

        energies: list[float] = []
        floor_samples: list[float] = []
        votes: list[bool] = []
        try:
            stream = sd.InputStream(samplerate=SR, blocksize=FRAME_N, channels=1, dtype="float32")
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._status = SensorStatus("audio", False, f"microphone unavailable: {exc}")
            return

        with stream:
            while not self._stop.is_set():
                try:
                    block, _overflow = stream.read(FRAME_N)
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        self._status = SensorStatus("audio", False, f"mic read error: {exc}")
                    time.sleep(1.0)
                    continue

                energy, dominance = features(np.asarray(block).reshape(-1))
                energies.append(energy)
                if len(energies) > FLOOR_LEN:
                    energies.pop(0)
                floor_samples.append(energy)
                if len(floor_samples) > FLOOR_LEN:
                    floor_samples.pop(0)
                # The room's quiet level: the 20th percentile of recent frames, so a long
                # conversation cannot drag the floor up and mute the sensor.
                floor = float(np.percentile(floor_samples, 20)) if len(floor_samples) >= 20 else 0.0

                now = time.time()
                votes.append(bool(floor > 0 and is_speech(energies, dominance, floor)))
                if len(votes) > VOTE_WINDOW:
                    votes.pop(0)
                if sum(votes) >= VOTE_HITS:
                    if now - self._last_speech > HOLD_S:
                        self._first_speech = now
                    self._last_speech = now

                speaking = self._last_speech > 0 and (now - self._last_speech) < HOLD_S
                self._publish(speaking, now, floor, energy, dominance)

    def _publish(self, speaking: bool, now: float, floor: float, energy: float, dominance: float) -> None:
        contacts: list[Contact] = []
        if speaking:
            quiet = now - self._last_speech
            contacts = [Contact(
                id="audio-voice",
                source="audio",
                distance_m=None,
                bearing_deg=None,          # impossible on a <=0.7 cm baseline
                confidence=0.65 if quiet < 1.5 else 0.45,
                moving=False,
                first_seen=self._first_speech or now,
                last_seen=self._last_speech,
                detail="voice heard" if quiet < 1.5 else f"voice {quiet:.0f}s ago",
            )]
        snr = 10 * np.log10((energy + 1e-20) / (floor + 1e-20)) if floor > 0 else 0.0
        with self._lock:
            self._contacts = contacts
            self._status = SensorStatus(
                "audio", True,
                f"{'speech' if speaking else 'quiet'}, {snr:+.0f} dB over floor, band {dominance:.0%}")


_sensor: Optional[PassiveSensor] = None


def sensor() -> PassiveSensor:
    global _sensor
    if _sensor is None:
        _sensor = PassiveSensor()
    return _sensor
