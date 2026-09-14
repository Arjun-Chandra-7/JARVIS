"""Local speech-to-text via faster-whisper (keyless, offline). Models load once, on first use.

Two transcripts come out of one utterance:

* **partial** — a fast, throwaway pass with a small model over the audio captured so far, so the
  HUD can show words while you are still speaking. It is allowed to be wrong.
* **committed** — the accurate pass over the whole utterance once you stop. This is what the brain
  is given and what gets stored.

They deliberately use different models. Measured on this laptop (n=9, background services running):
`tiny.en` greedy costs ~440 ms per partial update, `base.en` greedy transcribes a whole utterance
in ~550 ms, and the previous default (`small.en`, beam 5) took ~1.7 s.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np

_models: dict[tuple[str, str], object] = {}
_models_lock = threading.Lock()

PARTIAL_MODEL = "tiny.en"


def _get_model(model_name: str, compute_type: str = "int8"):
    key = (model_name, compute_type)
    with _models_lock:
        model = _models.get(key)
        if model is None:
            from faster_whisper import WhisperModel

            model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
            _models[key] = model
        return model


def warmup(model_name: str = "base.en", partial_model: Optional[str] = PARTIAL_MODEL) -> None:
    """Load the models ahead of time so the first transcription isn't slow."""
    _get_model(model_name)
    if partial_model:
        _get_model(partial_model)


def _to_float32(pcm_bytes: bytes, sample_rate: int) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if sample_rate != 16000:
        from math import gcd

        from scipy.signal import resample_poly

        divisor = gcd(sample_rate, 16000)
        audio = resample_poly(audio, 16000 // divisor, sample_rate // divisor)
    return np.clip(audio, -1.0, 1.0).astype(np.float32)


def transcribe(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    model_name: str = "base.en",
    beam_size: int = 1,
    language: str = "en",
    vocabulary: str = "Jarvis, Arjun, WhatsApp, VS Code, Codex, Claude, Antigravity, Opera GX, Google Meet",
) -> str:
    if not pcm_bytes:
        return ""
    audio = _to_float32(pcm_bytes, sample_rate)
    model = _get_model(model_name)
    segments, _info = model.transcribe(
        audio,
        language=None if language == "auto" else language,
        initial_prompt=vocabulary,
        beam_size=max(1, beam_size),
        vad_filter=True,  # faster-whisper's internal Silero VAD cleans non-speech
        condition_on_previous_text=False,
    )
    return " ".join(seg.text.strip() for seg in segments
                    if getattr(seg, "no_speech_prob", 0) < 0.7
                    and getattr(seg, "avg_logprob", 0) > -1.2).strip()


def transcribe_partial(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    model_name: str = PARTIAL_MODEL,
    vocabulary: str = "",
) -> str:
    """A quick, low-confidence read of speech captured so far. Greedy, no VAD filter, no cleanup.

    The VAD filter is off on purpose: the buffer is mid-utterance, and trimming it can drop the
    most recent word — the one the user most wants to see appear.
    """
    if not pcm_bytes:
        return ""
    audio = _to_float32(pcm_bytes, sample_rate)
    model = _get_model(model_name)
    segments, _info = model.transcribe(
        audio,
        language="en",
        initial_prompt=vocabulary or None,
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


class PartialTranscriber:
    """Runs partial transcriptions off the capture thread, at most one at a time.

    The audio capture loop must never block: a dropped microphone frame is a lost word. So
    `submit()` returns immediately and simply skips the update if the previous one is still
    running — partials are disposable, and skipping is better than queueing up stale work.
    """

    def __init__(
        self,
        on_text: Callable[[str], None],
        sample_rate: int = 16000,
        model_name: str = PARTIAL_MODEL,
        vocabulary: str = "",
    ) -> None:
        self._on_text = on_text
        self._sample_rate = sample_rate
        self._model_name = model_name
        self._vocabulary = vocabulary
        self._busy = threading.Lock()
        self._cancelled = threading.Event()
        self._last = ""

    def reset(self) -> None:
        self._cancelled.clear()
        self._last = ""

    def cancel(self) -> None:
        """Stop emitting: an in-flight partial will finish but its text is dropped."""
        self._cancelled.set()

    @property
    def last_text(self) -> str:
        return self._last

    def submit(self, pcm_bytes: bytes) -> bool:
        """Kick off a partial transcription. False means one was already running and this was skipped."""
        if self._cancelled.is_set():
            return False
        if not self._busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._run, args=(pcm_bytes,), daemon=True).start()
        return True

    def _run(self, pcm_bytes: bytes) -> None:
        try:
            text = transcribe_partial(
                pcm_bytes, self._sample_rate, self._model_name, self._vocabulary
            )
            if text and not self._cancelled.is_set() and text != self._last:
                self._last = text
                self._on_text(text)
        except Exception:  # noqa: BLE001 - a failed partial must never break capture
            pass
        finally:
            self._busy.release()
