"""Make every line equally loud, and never louder than the speakers can take.

Measured on this machine: the speaker volume sat at 153%. PipeWire's volume is cubic, so that is
a gain of 3.58 on everything played — and at that gain 30% of the voiced 10 ms frames of Kokoro's
output clipped. Distortion, not the voice, was a large part of "hard to understand". Kokoro's
lines also came out 4–5 dB quieter than Piper's (−21 against −16 dBFS), which is presumably why
the volume had been pushed past 100% in the first place.

So each chunk is brought to one loudness, and then its peak is held under what the sink can pass
at its current gain. A clean gain change per chunk — no compressor, nothing that colours the
voice — and the system volume is left exactly as the person set it.
"""
from __future__ import annotations

import subprocess
import threading
import time

import numpy as np

TARGET_DBFS = -18.0          # speech RMS, before the sink's own gain
CEILING = 0.93               # peak allowed after the sink's gain: a little headroom below 1.0
MAX_BOOST = 4.0              # a near-silent chunk is not turned into noise

_cache = {"at": 0.0, "gain": 1.0}
_lock = threading.Lock()


def parse_volume(text: str) -> float:
    """``wpctl get-volume`` output → linear amplitude gain. "Volume: 1.53" → 1.53³."""
    try:
        value = float(text.split("Volume:", 1)[1].split()[0])
    except (IndexError, ValueError):
        return 1.0
    if "[MUTED]" in text:
        return 1.0
    return max(0.0, value) ** 3


def sink_gain(max_age_s: float = 5.0) -> float:
    """The default sink's software gain, read at most every few seconds. 1.0 when unknown."""
    with _lock:
        if time.monotonic() - _cache["at"] < max_age_s:
            return _cache["gain"]
    try:
        out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True,
                             text=True, timeout=0.5).stdout
        gain = parse_volume(out)
    except (OSError, subprocess.SubprocessError):
        gain = 1.0
    with _lock:
        _cache.update(at=time.monotonic(), gain=gain)
    return gain


def shape(pcm: bytes, gain: float = 1.0, target_dbfs: float = TARGET_DBFS) -> bytes:
    """One chunk of int16 speech at the target loudness, its peak kept below the ceiling that the
    sink's ``gain`` leaves. Pure: the same input always gives the same output."""
    if not pcm:
        return pcm
    a = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32) / 32768.0
    if not a.size:
        return pcm
    rms = float(np.sqrt(np.mean(a * a)))
    peak = float(np.abs(a).max())
    if rms < 1e-4 or peak < 1e-4:
        return pcm
    wanted = min(MAX_BOOST, (10 ** (target_dbfs / 20.0)) / rms)
    ceiling = CEILING / max(1.0, gain)
    applied = min(wanted, ceiling / peak)
    return (np.clip(a * applied, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
