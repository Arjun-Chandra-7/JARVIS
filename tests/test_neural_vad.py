"""Silero speech detection, and the energy fallback when it isn't available."""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio import neural_vad, vad


class _FakeSession:
    """Stands in for onnxruntime: reports speech whenever the window is loud enough."""

    def __init__(self, prob_for=lambda chunk: 0.0):
        self.prob_for = prob_for
        self.calls = 0
        self.window_sizes: list[int] = []

    def run(self, _outputs, feeds):
        self.calls += 1
        chunk = feeds["input"]
        self.window_sizes.append(chunk.shape[-1])
        p = float(self.prob_for(chunk))
        # onnxruntime returns a flat list of outputs, which the caller unpacks as (prob, state).
        return [np.array([[p]], dtype=np.float32), feeds["state"]]


def _loud(chunk):
    return 0.95 if float(np.abs(chunk).mean()) > 0.05 else 0.01


def test_feed_slices_input_into_silero_windows():
    det = neural_vad.SileroVAD(_FakeSession(), 16000)
    det.feed(np.zeros(1280, dtype=np.int16))     # openWakeWord's frame size
    assert det.session.window_sizes == [512, 512]


def test_partial_frames_are_buffered_not_dropped():
    session = _FakeSession()
    det = neural_vad.SileroVAD(session, 16000)
    det.feed(np.zeros(300, dtype=np.int16))
    assert session.calls == 0                     # not yet a full window
    det.feed(np.zeros(300, dtype=np.int16))
    assert session.calls == 1                     # 600 samples -> one 512 window, 88 held over


def test_speech_probability_is_reported():
    det = neural_vad.SileroVAD(_FakeSession(_loud), 16000)
    quiet = np.zeros(512, dtype=np.int16)
    loud = (np.ones(512) * 8000).astype(np.int16)
    assert det.feed(quiet) < 0.5
    assert det.feed(loud) > 0.5


def test_short_frame_keeps_the_previous_answer():
    det = neural_vad.SileroVAD(_FakeSession(_loud), 16000)
    det.feed((np.ones(512) * 8000).astype(np.int16))
    assert det.feed(np.zeros(10, dtype=np.int16)) > 0.5   # too short to re-decide


def test_reset_clears_buffered_audio_and_state():
    session = _FakeSession()
    det = neural_vad.SileroVAD(session, 16000)
    det.feed(np.zeros(400, dtype=np.int16))
    det.reset()
    det.feed(np.zeros(200, dtype=np.int16))
    assert session.calls == 0        # the 400 leftover samples were discarded, not carried over
    assert det.last_prob == 0.0


def test_detector_falls_back_to_energy_when_the_model_is_absent(monkeypatch):
    monkeypatch.setattr(neural_vad, "load", lambda *_a, **_k: None)
    fn, reset, label = vad.speech_detector(threshold=100.0)
    assert label == "energy"
    reset()
    assert fn(np.zeros(512, dtype=np.int16), 500.0) is True
    assert fn(np.zeros(512, dtype=np.int16), 10.0) is False


def test_detector_uses_the_model_when_present(monkeypatch):
    monkeypatch.setattr(neural_vad, "load", lambda *_a, **_k: neural_vad.SileroVAD(_FakeSession(_loud), 16000))
    fn, _reset, label = vad.speech_detector(threshold=100.0)
    assert label == "silero"
    loud = (np.ones(512) * 8000).astype(np.int16)
    # Loud enough to pass the energy pre-filter, and the model calls it speech.
    assert fn(loud, 500.0) is True
    # Same audio, but below the pre-filter: never reaches the model.
    assert fn(loud, 5.0) is False


def test_a_loud_non_speech_frame_is_rejected_by_the_model(monkeypatch):
    # The whole point: energy alone would open an utterance on a desk knock.
    monkeypatch.setattr(neural_vad, "load",
                        lambda *_a, **_k: neural_vad.SileroVAD(_FakeSession(lambda c: 0.02), 16000))
    fn, _reset, _label = vad.speech_detector(threshold=100.0)
    bang = (np.ones(512) * 20000).astype(np.int16)
    assert fn(bang, 20000.0) is False


def test_record_utterance_honours_a_custom_speech_fn():
    frames = [np.zeros(512, dtype=np.int16)] * 2 + [(np.ones(512) * 6000).astype(np.int16)] * 12 \
        + [np.zeros(512, dtype=np.int16)] * 12
    it = iter(frames)
    # Energy would call every frame silent (threshold far above); the custom fn decides instead.
    pcm = vad.record_utterance(
        lambda: next(it, None),
        sample_rate=16000, frame_length=512, threshold=1e9,
        silence_ms=200, max_s=5, wait_s=1, min_speech_ms=100,
        speech_fn=lambda frame, level: level > 1000,
    )
    assert pcm is not None and len(pcm) > 0


def test_enabled_respects_the_env_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_NEURAL_VAD", "0")
    assert neural_vad.enabled() is False
    monkeypatch.setenv("JARVIS_NEURAL_VAD", "1")
    assert neural_vad.enabled() is True


def test_load_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("JARVIS_NEURAL_VAD", "off")
    neural_vad._CACHED.clear()
    assert neural_vad.load() is None


@pytest.mark.skipif(not neural_vad.model_path().exists(), reason="model not downloaded")
def test_real_model_rejects_silence_noise_and_tone():
    """The claim this module is built on, checked against the actual network."""
    det = neural_vad.load(16000)
    assert det is not None
    rng = np.random.default_rng(0)
    cases = {
        "silence": np.zeros(1536, dtype=np.int16),
        "white noise": (rng.standard_normal(1536) * 900).astype(np.int16),
        "pure tone": (np.sin(np.arange(1536) * 0.31) * 9000).astype(np.int16),
    }
    for name, pcm in cases.items():
        det.reset()
        assert det.feed(pcm) < 0.5, f"{name} was classified as speech"
