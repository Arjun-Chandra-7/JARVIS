"""Energy-based voice-activity detection and utterance capture.

Deliberately dependency-light (numpy only): no webrtcvad build, no fixed frame-size rules — it
works on whatever frame length the wake-word engine uses. Good enough for a wake-word-gated
assistant; swap in a neural VAD later if needed. Pure functions here are unit-tested offline.
"""

from __future__ import annotations

from typing import Callable, Optional
from collections import deque

import numpy as np

# A source of audio frames: each call returns a sequence of int16 samples (or None to stop).
FrameReader = Callable[[], Optional[object]]


def rms(frame) -> float:
    arr = np.asarray(frame, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(arr))))


def speech_detector(threshold: float, sample_rate: int = 16000, prob: float = 0.5):
    """Build the `speech_fn` for `record_utterance`.

    Silero when the model is available, energy otherwise. The energy gate is kept as a cheap
    pre-filter even on the neural path: running the classifier on frames that are inaudibly quiet
    wastes CPU and, more importantly, a very low-level frame that Silero happens to score above
    0.5 is not something the microphone can usefully transcribe anyway.

    Returns (speech_fn, reset_fn, label). `reset_fn` must be called before each utterance — Silero
    carries recurrent state, and a stale one biases the opening frames of the next phrase.
    """
    from . import neural_vad

    detector = neural_vad.load(sample_rate)
    if detector is None:
        def by_energy(_frame, level):  # noqa: ANN001
            return level >= threshold
        return by_energy, (lambda: None), "energy"

    floor = threshold * 0.35

    def by_model(frame, level):  # noqa: ANN001
        if level < floor:
            return False
        return detector.feed(frame) >= prob

    return by_model, detector.reset, "silero"


def calibrate_threshold(
    read_frame: FrameReader,
    frames: int = 15,
    floor: float = 110.0,
    factor: float = 2.0,
) -> float:
    """Sample ambient noise for a moment and derive a speech threshold above it."""
    levels = []
    for _ in range(frames):
        frame = read_frame()
        if frame is None:
            break
        levels.append(rms(frame))
    ambient = float(np.mean(levels)) if levels else 0.0
    return max(floor, ambient * factor)


def record_utterance(
    read_frame: FrameReader,
    *,
    sample_rate: int,
    frame_length: int,
    threshold: float,
    silence_ms: int,
    max_s: float,
    wait_s: float,
    min_speech_ms: int = 200,
    on_level: Optional[Callable[[float], None]] = None,
    speech_fn: Optional[Callable[[object, float], bool]] = None,
) -> Optional[bytes]:
    """Capture one spoken phrase.

    Waits up to `wait_s` for speech to start; once it does, accumulates frames until `silence_ms`
    of trailing silence (or `max_s` total). Returns int16 PCM bytes, or None if no speech began
    within the wait window.

    `on_level` is called with each frame's RMS as it arrives. This is the only place in the system
    that sees live microphone amplitude, so it is how the HUD gets a waveform that reflects the
    actual room rather than a sine wave pretending to.

    `speech_fn(frame, rms) -> bool` decides what counts as speech. The default is the RMS
    threshold; `speech_detector()` returns a Silero-backed one when the model is available, which
    stops a keyboard or a fan from opening an utterance.
    """
    if speech_fn is None:
        def speech_fn(_frame, level):  # noqa: ANN001 - local default, mirrors the old behaviour
            return level >= threshold
    frame_ms = 1000.0 * frame_length / sample_rate
    silence_needed = max(1, int(round(silence_ms / frame_ms)))
    max_frames = max(1, int(round(max_s * 1000.0 / frame_ms)))
    wait_frames = max(1, int(round(wait_s * 1000.0 / frame_ms)))
    min_speech = max(1, int(round(min_speech_ms / frame_ms)))

    collected: list[np.ndarray] = []
    started = False
    silent_run = 0
    waited = 0
    speech_frames = 0
    # Preserve quiet consonants immediately before the energy threshold is crossed.
    preroll = deque(maxlen=max(1, round(300 / frame_ms)))

    while True:
        frame = read_frame()
        if frame is None:
            break
        arr = np.asarray(frame, dtype=np.int16)
        level = rms(arr)
        if on_level is not None:
            try:
                on_level(level)
            except Exception:  # noqa: BLE001 - a HUD hiccup must never break capture
                pass

        voiced = speech_fn(arr, level)

        if not started:
            if voiced:
                started = True
                collected.extend(preroll)
                collected.append(arr)
                speech_frames += 1
            else:
                preroll.append(arr.copy())
                waited += 1
                if waited >= wait_frames:
                    return None
        else:
            collected.append(arr)
            if not voiced:
                silent_run += 1
                if silent_run >= silence_needed:
                    break
            else:
                silent_run = 0
                speech_frames += 1
            if len(collected) >= max_frames:
                break

    # reject noise blips: need a minimum amount of actual speech
    if not collected or speech_frames < min_speech:
        return None
    return np.concatenate(collected).astype(np.int16).tobytes()
