"""Local speech-to-text via faster-whisper (keyless, offline). Model loads once, on first use."""

from __future__ import annotations

import numpy as np

_model = None
_model_name = None


def warmup(model_name: str = "small.en") -> None:
    """Load the model ahead of time so the first transcription isn't slow."""
    _get_model(model_name)


def _get_model(model_name: str):
    global _model, _model_name
    if _model is None or _model_name != model_name:
        from faster_whisper import WhisperModel

        _model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _model_name = model_name
    return _model


def transcribe(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    model_name: str = "small.en",
    beam_size: int = 5,
) -> str:
    if not pcm_bytes:
        return ""
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    model = _get_model(model_name)
    segments, _info = model.transcribe(
        audio,
        language="en",
        beam_size=beam_size,
        vad_filter=True,  # faster-whisper's internal Silero VAD cleans non-speech
        condition_on_previous_text=False,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()
