"""Energy-based voice-activity detection and utterance capture.

Deliberately dependency-light (numpy only): no webrtcvad build, no fixed frame-size rules — it
works on whatever frame length the wake-word engine uses. Good enough for a wake-word-gated
assistant; swap in a neural VAD later if needed. Pure functions here are unit-tested offline.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

# A source of audio frames: each call returns a sequence of int16 samples (or None to stop).
FrameReader = Callable[[], Optional[object]]


def rms(frame) -> float:
    arr = np.asarray(frame, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(arr))))


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
) -> Optional[bytes]:
    """Capture one spoken phrase.

    Waits up to `wait_s` for speech to start; once it does, accumulates frames until `silence_ms`
    of trailing silence (or `max_s` total). Returns int16 PCM bytes, or None if no speech began
    within the wait window.
    """
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

    while True:
        frame = read_frame()
        if frame is None:
            break
        arr = np.asarray(frame, dtype=np.int16)
        level = rms(arr)

        if not started:
            if level >= threshold:
                started = True
                collected.append(arr)
                speech_frames += 1
            else:
                waited += 1
                if waited >= wait_frames:
                    return None
        else:
            collected.append(arr)
            if level < threshold:
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
