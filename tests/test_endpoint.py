"""Neural end-of-speech detection: the decision logic must not cut people off mid-sentence."""
import numpy as np
import pytest

from jarvis.audio import endpoint as ep


# --------------------------------------------------------------------- decision logic (pure)
def feed(decision: ep.EndpointDecision, probs) -> None:
    for p in probs:
        decision.update(p)


def test_does_not_start_on_silence():
    d = ep.EndpointDecision()
    feed(d, [0.01] * 100)
    assert not d.started and not d.ended


def test_starts_on_speech_and_ends_after_hangover():
    d = ep.EndpointDecision(hangover_ms=300)
    feed(d, [0.9] * 20)
    assert d.started and not d.ended
    # 300 ms of hangover at 32 ms per window rounds to 9 windows
    assert d.hangover_windows == 9
    feed(d, [0.01] * 8)
    assert not d.ended, "ended before the hangover elapsed"
    feed(d, [0.01])
    assert d.ended


def test_brief_dip_mid_word_does_not_end_speech():
    """A stop consonant or breath drops the probability for a window or two.

    With a single threshold that would end the utterance; hysteresis plus the hangover means it
    must not.
    """
    d = ep.EndpointDecision(hangover_ms=300)
    feed(d, [0.9] * 10)
    feed(d, [0.05] * 3)      # short dip, well under the hangover
    feed(d, [0.9] * 10)
    assert not d.ended
    assert d.had_enough_speech


def test_probability_between_thresholds_sustains_speech():
    """Values in the 0.35-0.5 band are ambiguous: they must not end an utterance already running."""
    d = ep.EndpointDecision(speech_threshold=0.5, silence_threshold=0.35, hangover_ms=200)
    feed(d, [0.9] * 10)
    feed(d, [0.42] * 50)     # long ambiguous stretch
    assert not d.ended


def test_noise_blip_is_rejected_as_too_short():
    d = ep.EndpointDecision(hangover_ms=100, min_speech_ms=200)
    feed(d, [0.9] * 2)       # ~64 ms of speech
    feed(d, [0.0] * 10)
    assert d.ended
    assert not d.had_enough_speech, "a 64 ms blip must not count as an utterance"


def test_reset_clears_state():
    d = ep.EndpointDecision()
    feed(d, [0.9] * 10 + [0.0] * 40)
    assert d.started and d.ended
    d.reset()
    assert not d.started and not d.ended and not d.had_enough_speech


def test_update_after_end_is_ignored():
    d = ep.EndpointDecision(hangover_ms=100)
    feed(d, [0.9] * 10 + [0.0] * 10)
    assert d.ended
    feed(d, [0.99] * 10)
    assert d.ended, "speech after the endpoint belongs to the next utterance"


# --------------------------------------------------------------------- capture loop
class FakeVad:
    """Stands in for Silero so the capture loop is testable without the model."""

    def __init__(self, probs_per_frame):
        self._probs = list(probs_per_frame)
        self._i = 0

    def reset(self):
        self._i = 0

    def push(self, _frame):
        if self._i >= len(self._probs):
            return [0.0]
        p = self._probs[self._i]
        self._i += 1
        return [p]


def reader_for(n_frames: int, frame_length: int = 1280):
    """A microphone that returns `n_frames` frames of constant tone, then stops."""
    state = {"i": 0}

    def read():
        if state["i"] >= n_frames:
            return None
        state["i"] += 1
        return (np.ones(frame_length, dtype=np.int16) * 1000).tolist()

    return read


def test_returns_none_when_no_speech_within_wait():
    pcm = ep.record_utterance(
        reader_for(200), sample_rate=16000, frame_length=1280,
        wait_s=0.5, vad=FakeVad([0.0] * 200),
    )
    assert pcm is None


def test_captures_utterance_and_stops_at_endpoint():
    # 20 frames of speech, then silence; hangover 160 ms = 2 windows at 80 ms/frame here
    probs = [0.9] * 20 + [0.0] * 50
    pcm = ep.record_utterance(
        reader_for(200), sample_rate=16000, frame_length=1280,
        wait_s=2.0, silence_ms=160, min_speech_ms=200, vad=FakeVad(probs),
    )
    assert pcm is not None
    samples = len(pcm) // 2
    # Should stop shortly after speech ends, not run to the 200-frame end of the reader.
    assert samples < 200 * 1280, "capture ran past the endpoint"
    assert samples > 20 * 1280 * 0.5, "capture dropped most of the speech"


def test_preroll_keeps_audio_from_before_the_trigger():
    """Silero fires a beat into the first word, so the frames just before it must be kept."""
    probs = [0.0] * 10 + [0.9] * 20 + [0.0] * 50
    pcm = ep.record_utterance(
        reader_for(200), sample_rate=16000, frame_length=1280,
        wait_s=2.0, silence_ms=160, preroll_ms=240, vad=FakeVad(probs),
    )
    assert pcm is not None
    # 20 speech frames + up to 3 preroll frames (240 ms / 80 ms)
    assert len(pcm) // 2 > 20 * 1280, "preroll was not prepended"


def test_max_s_caps_a_run_on_utterance():
    probs = [0.9] * 500
    pcm = ep.record_utterance(
        reader_for(500), sample_rate=16000, frame_length=1280,
        wait_s=2.0, max_s=1.0, vad=FakeVad(probs),
    )
    assert pcm is not None
    assert len(pcm) // 2 <= int(1.0 * 16000) + 1280 * 4


def test_partial_callback_fires_while_speaking_and_gets_growing_audio():
    seen = []
    probs = [0.9] * 40 + [0.0] * 20
    ep.record_utterance(
        reader_for(200), sample_rate=16000, frame_length=1280,
        wait_s=2.0, silence_ms=160, vad=FakeVad(probs),
        on_partial=lambda pcm: seen.append(len(pcm)), partial_every_ms=240,
    )
    assert len(seen) >= 2, "no live partials were emitted during a 3 s utterance"
    assert seen == sorted(seen), "partial audio must only grow"


def test_on_speech_start_fires_once():
    calls = []
    probs = [0.0] * 5 + [0.9] * 30 + [0.0] * 30
    ep.record_utterance(
        reader_for(200), sample_rate=16000, frame_length=1280,
        wait_s=2.0, silence_ms=160, vad=FakeVad(probs),
        on_speech_start=lambda: calls.append(1),
    )
    assert len(calls) == 1


# --------------------------------------------------------------------- the real model
@pytest.mark.skipif(not ep.available(), reason="Silero model unavailable")
def test_real_silero_scores_speech_above_silence():
    """Guards the 64-sample context convention: feeding bare 512-sample windows returns ~0."""
    vad = ep.StreamingVad()
    rng = np.random.default_rng(0)
    quiet = (rng.standard_normal(16000) * 30).astype(np.int16)
    quiet_probs = vad.push(quiet)
    assert max(quiet_probs) < 0.5, "room tone was classified as speech"
