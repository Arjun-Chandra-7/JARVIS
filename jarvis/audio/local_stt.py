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
    language: str = "en",
    vocabulary: str = "Jarvis, Arjun, WhatsApp, VS Code, Codex, Claude, Antigravity, Opera GX, Google Meet",
) -> str:
    if not pcm_bytes:
        return ""
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if sample_rate != 16000:
        from scipy.signal import resample_poly
        from math import gcd
        divisor = gcd(sample_rate, 16000)
        audio = resample_poly(audio, 16000 // divisor, sample_rate // divisor)
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
