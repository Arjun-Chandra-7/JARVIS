"""Reported: "speech recognition keeps losing what I said when the GPU is busy".

The card is shared with Ollama. When it is full, CTranslate2 fails a transcription with
"cuBLAS failed with status CUBLAS_STATUS_ALLOC_FAILED" (seen twice in this machine's test runs,
once as CUBLAS_STATUS_NOT_SUPPORTED) instead of an "out of memory" error, and the fallback to
the processor only recognised the latter — so the utterance raised instead of being heard.
"""
import numpy as np
import pytest

from jarvis.audio import local_stt


class Segment:
    def __init__(self, text):
        self.text = text
        self.no_speech_prob = 0.0
        self.avg_logprob = -0.1


class BusyGPU:
    def __init__(self, status):
        self.status = status

    def transcribe(self, audio, **kwargs):
        status = self.status

        def segments():
            raise RuntimeError(f"cuBLAS failed with status {status}")
            yield  # pragma: no cover - makes this a generator, like faster-whisper's

        return segments(), None


class Processor:
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, **kwargs):
        return iter([Segment("what is my battery level")]), None


@pytest.mark.parametrize("status", ["CUBLAS_STATUS_ALLOC_FAILED", "CUBLAS_STATUS_NOT_SUPPORTED"])
def test_a_busy_gpu_does_not_lose_what_was_said(monkeypatch, status):
    import faster_whisper

    monkeypatch.setattr(local_stt, "_get_model", lambda name, compute_type="int8": BusyGPU(status))
    monkeypatch.setattr(faster_whisper, "WhisperModel", Processor)
    monkeypatch.setattr(local_stt, "_models", {})
    one_second = np.zeros(16000, dtype=np.int16).tobytes()
    assert local_stt.transcribe(one_second, 16000, "base") == "what is my battery level"


def test_an_unrelated_error_is_still_raised(monkeypatch):
    class Broken:
        def transcribe(self, audio, **kwargs):
            raise RuntimeError("bad audio shape")

    monkeypatch.setattr(local_stt, "_get_model", lambda name, compute_type="int8": Broken())
    with pytest.raises(RuntimeError, match="bad audio shape"):
        local_stt.transcribe(np.zeros(1600, dtype=np.int16).tobytes(), 16000, "base")
