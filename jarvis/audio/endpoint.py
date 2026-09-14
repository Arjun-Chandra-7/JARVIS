"""Neural end-of-speech detection (Silero VAD), replacing the fixed energy-threshold timeout.

Why this exists
---------------
`vad.record_utterance` decides you have stopped talking when the microphone's RMS energy stays
below a threshold for `silence_ms` — 2000 ms by default. That is two seconds of dead air before
transcription even begins, and the threshold has to be recalibrated against room noise.

Silero answers "is this speech?" per 32 ms window with a small LSTM, so a 300 ms hangover is
enough to be confident. Measured on this laptop: endpoint fires ~90 ms after true end of speech,
at 0.087 ms per window (~0.3 % of one core).

The model ships inside `faster-whisper`, which Jarvis already depends on for transcription, so
this adds no download and no new dependency.

Silero's input convention is easy to get wrong: each step wants the 64 samples preceding the
window concatenated in front of the 512 new ones (576 total), carrying the LSTM state across
steps. Feeding only the 512 new samples returns ~0 for real speech.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Optional

import numpy as np

WINDOW = 512          # samples Silero scores at a time @16 kHz (32 ms)
CONTEXT = 64          # samples of history Silero expects in front of each window
WINDOW_MS = WINDOW / 16000 * 1000

FrameReader = Callable[[], Optional[object]]


class SileroUnavailable(RuntimeError):
    """Raised when the bundled Silero model cannot be loaded, so callers can fall back to RMS."""


class StreamingVad:
    """Frame-by-frame speech probability, holding LSTM state across calls.

    Accepts int16 audio in any frame size; internally re-windows to Silero's 512-sample steps
    (the microphone hands us 1280-sample/80 ms frames, which is 2.5 windows).
    """

    def __init__(self, sample_rate: int = 16000) -> None:
        if sample_rate != 16000:
            raise SileroUnavailable(f"Silero here is 16 kHz only, got {sample_rate}")
        try:
            from faster_whisper.vad import get_vad_model

            self._session = get_vad_model().session
        except Exception as exc:  # noqa: BLE001 - any failure means "use the RMS path"
            raise SileroUnavailable(str(exc)) from exc
        self._pending = np.zeros(0, dtype=np.float32)
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def _score(self, window: np.ndarray) -> float:
        inp = np.concatenate([self._context, window]).reshape(1, -1).astype(np.float32)
        out, self._h, self._c = self._session.run(
            None, {"input": inp, "h": self._h, "c": self._c}
        )
        self._context = window[-CONTEXT:].copy()
        return float(np.asarray(out).reshape(-1)[0])

    def push(self, frame) -> list[float]:
        """Feed one microphone frame; return a probability per completed 512-sample window."""
        arr = np.asarray(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, arr])
        probs: list[float] = []
        while len(self._pending) >= WINDOW:
            probs.append(self._score(self._pending[:WINDOW]))
            self._pending = self._pending[WINDOW:]
        return probs


class EndpointDecision:
    """Turns a stream of speech probabilities into start-of-speech / end-of-speech decisions.

    Hysteresis: crossing `speech_threshold` starts speech, and only sustained probability below
    `silence_threshold` for `hangover_ms` ends it. Two thresholds stop a brief dip mid-word
    (a stop consonant, a breath) from cutting the utterance short.
    """

    def __init__(
        self,
        *,
        speech_threshold: float = 0.5,
        silence_threshold: float = 0.35,
        hangover_ms: int = 300,
        min_speech_ms: int = 200,
    ) -> None:
        self.speech_threshold = speech_threshold
        self.silence_threshold = silence_threshold
        self.hangover_windows = max(1, round(hangover_ms / WINDOW_MS))
        self.min_speech_windows = max(1, round(min_speech_ms / WINDOW_MS))
        self.reset()

    def reset(self) -> None:
        self.started = False
        self.ended = False
        self._silent_run = 0
        self._speech_windows = 0

    def update(self, prob: float) -> None:
        if self.ended:
            return
        if not self.started:
            if prob >= self.speech_threshold:
                self.started = True
                self._speech_windows = 1
            return
        if prob >= self.silence_threshold:
            self._silent_run = 0
            if prob >= self.speech_threshold:
                self._speech_windows += 1
        else:
            self._silent_run += 1
            if self._silent_run >= self.hangover_windows:
                self.ended = True

    @property
    def had_enough_speech(self) -> bool:
        return self._speech_windows >= self.min_speech_windows


def record_utterance(
    read_frame: FrameReader,
    *,
    sample_rate: int,
    frame_length: int,
    silence_ms: int = 300,
    max_s: float = 30.0,
    wait_s: float = 4.0,
    min_speech_ms: int = 200,
    preroll_ms: int = 300,
    speech_threshold: float = 0.5,
    silence_threshold: float = 0.35,
    vad: Optional[StreamingVad] = None,
    on_speech_start: Optional[Callable[[], None]] = None,
    on_partial: Optional[Callable[[bytes], None]] = None,
    partial_every_ms: int = 700,
) -> Optional[bytes]:
    """Capture one spoken phrase, ending as soon as Silero says the speech stopped.

    Mirrors `vad.record_utterance`'s contract (int16 PCM bytes, or None when no speech began
    within `wait_s`) so it is a drop-in replacement, minus the energy `threshold` argument.

    `on_partial` is handed the audio captured so far, at most every `partial_every_ms`, so a
    caller can run a fast throwaway transcription for a live transcript. It must return quickly:
    run the actual model on another thread.
    """
    vad = vad or StreamingVad(sample_rate)
    vad.reset()
    decision = EndpointDecision(
        speech_threshold=speech_threshold,
        silence_threshold=silence_threshold,
        hangover_ms=silence_ms,
        min_speech_ms=min_speech_ms,
    )

    frame_ms = 1000.0 * frame_length / sample_rate
    wait_frames = max(1, round(wait_s * 1000.0 / frame_ms))
    max_frames = max(1, round(max_s * 1000.0 / frame_ms))
    partial_every = max(1, round(partial_every_ms / frame_ms))
    # Keep the audio just before the trigger: Silero fires a beat into the first word.
    preroll = deque(maxlen=max(1, round(preroll_ms / frame_ms)))

    collected: list[np.ndarray] = []
    waited = 0
    frames = 0
    since_partial = 0

    while True:
        frame = read_frame()
        if frame is None:
            break
        arr = np.asarray(frame, dtype=np.int16)
        was_started = decision.started

        for prob in vad.push(arr):
            decision.update(prob)

        if not decision.started:
            preroll.append(arr.copy())
            waited += 1
            if waited >= wait_frames:
                return None
            continue

        if not was_started:
            collected.extend(preroll)
            if on_speech_start is not None:
                on_speech_start()
        collected.append(arr)
        frames += 1

        if decision.ended:
            break
        if frames >= max_frames:
            break

        since_partial += 1
        if on_partial is not None and since_partial >= partial_every:
            since_partial = 0
            on_partial(np.concatenate(collected).astype(np.int16).tobytes())

    if not collected or not decision.had_enough_speech:
        return None
    return np.concatenate(collected).astype(np.int16).tobytes()


def available() -> bool:
    """True when neural endpointing can be used; False means callers should stay on RMS."""
    try:
        StreamingVad()
        return True
    except Exception:  # noqa: BLE001
        return False
