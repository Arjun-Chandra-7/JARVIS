"""Local speech-to-text via faster-whisper (keyless, offline). Models load once, on first use.

Two transcripts come out of one utterance:

* **partial** — a fast, throwaway pass with a small model over the audio captured so far, so the
  HUD can show words while you are still speaking. It is allowed to be wrong.
* **committed** — the accurate pass over the whole utterance once you stop. This is what the brain
  is given and what gets stored.

They deliberately use different models. Measured on this laptop (n=9, background services running):
`tiny` greedy costs ~440 ms per partial update, `base` greedy transcribes a whole utterance
in ~550 ms, and the previous default (`small.en`, beam 5) took ~1.7 s.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

import numpy as np

_models: dict[tuple[str, str], object] = {}
_models_lock = threading.Lock()

# Multilingual, like the committed model: "tiny.en" cannot read Hindi at all, and it
# is the partial that the user watches appear while they are still speaking.
PARTIAL_MODEL = "tiny"


def best_model() -> str:
    """The largest model that is worth running here.

    On the processor "small" takes four seconds, which is too slow to talk to; on the GPU it takes
    a fifth of a second and hears more than "base" ever did. So the choice follows the hardware.
    """
    override = os.environ.get("JARVIS_WHISPER_MODEL")
    if override:
        return override
    return "small" if _cuda_usable() else "base"


def _cuda_usable() -> bool:
    """Whether a GPU is present and Jarvis is allowed to use it."""
    if os.environ.get("JARVIS_STT_DEVICE", "").lower() == "cpu":
        return False
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def _get_model(model_name: str, compute_type: str = "int8"):
    """The model, on the GPU when there is one.

    Measured on this machine, on the same five spoken commands:

        base   cpu  int8   1.25 s   3/5 transcribed exactly
        small  cuda int8   0.22 s   4/5

    which is why the default model is now the larger one: on the GPU it is both quicker and
    better than the small one was on the CPU. float16 is deliberately not used — this venv has no
    cuBLAS, and asking for it fails with CUBLAS_STATUS_NOT_SUPPORTED while int8 works.

    The card is shared with Ollama, which holds three of its four gigabytes, so a model that does
    not fit falls back to the processor rather than failing the utterance.
    """
    key = (model_name, compute_type)
    with _models_lock:
        model = _models.get(key)
        if model is not None:
            return model

        from faster_whisper import WhisperModel

        if _cuda_usable():
            try:
                model = WhisperModel(model_name, device="cuda", compute_type=compute_type)
                _models[key] = model
                return model
            except Exception as exc:  # noqa: BLE001 - out of memory, no cuBLAS, no driver
                print(f"  speech: GPU unavailable for {model_name} ({type(exc).__name__}); "
                      f"using the processor")

        model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
        _models[key] = model
        return model


def _transcribe(model_name: str, audio, **kwargs):
    """Transcribe, and if the GPU refuses mid-utterance, do it on the processor instead.

    Construction succeeding does not mean transcription will: the card is shared with Ollama, and
    a model that loaded when there was room can still run out of it a minute later. Falling back
    at the point of failure keeps the utterance rather than losing it — the only cost is that one
    sentence is slower.
    """
    def run(model):
        # faster_whisper hands back a lazy generator, so the work — and the failure — happens on
        # iteration, not on the call. Consuming it here is what puts it inside the guard below.
        segments, info = model.transcribe(audio, **kwargs)
        return list(segments), info

    try:
        return run(_get_model(model_name))
    except RuntimeError as exc:
        if "out of memory" not in str(exc).lower():
            raise
        from faster_whisper import WhisperModel

        with _models_lock:
            _models.pop((model_name, "int8"), None)
            fallback = _models.get((model_name, "cpu"))
            if fallback is None:
                fallback = WhisperModel(model_name, device="cpu", compute_type="int8")
                _models[(model_name, "cpu")] = fallback
        return run(fallback)


def warmup(model_name: str = "base", partial_model: Optional[str] = PARTIAL_MODEL) -> None:
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
    model_name: str = "base",
    beam_size: int = 1,
    language: str = "en",
    vocabulary: str = "Jarvis, Arjun, WhatsApp, VS Code, Codex, Claude, Antigravity, Opera GX, Google Meet",
) -> str:
    if not pcm_bytes:
        return ""
    audio = _to_float32(pcm_bytes, sample_rate)
    segments, _info = _transcribe(
        model_name,
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
    segments, _info = _transcribe(
        model_name,
        audio,
        # Hard-coded "en" here meant the live partial was always read as English even when the
        # committed transcript was not — so a Hindi sentence appeared as English gibberish while
        # it was being spoken. None lets Whisper decide, as the committed pass does.
        language=None,
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
