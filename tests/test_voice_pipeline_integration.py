"""End-to-end voice capture: synthesised speech in, committed transcript out.

Runs the real Silero endpointer and the real faster-whisper model over a fake microphone, so the
whole chain — re-windowing 1280-sample mic frames into Silero's 512-sample steps, hysteresis,
hangover, preroll, partial scheduling, transcription — is exercised without audio hardware.

Skipped automatically when the models are not available, so the suite still runs on a machine
that has never downloaded them.
"""
import numpy as np
import pytest

from jarvis.audio import endpoint

pytestmark = pytest.mark.skipif(not endpoint.available(), reason="Silero model unavailable")

FRAME = 1280  # what the wake-word engine hands the session, 80 ms at 16 kHz


def piper_speech(text: str):
    """Synthesised speech at 16 kHz, or None when Piper is not usable here."""
    try:
        from jarvis.audio import local_tts
        from jarvis.config import CONFIG

        pcm, sr = local_tts.synth(text, CONFIG.piper_model)
        if not pcm:
            return None
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if sr != 16000:
            from math import gcd

            from scipy.signal import resample_poly

            d = gcd(sr, 16000)
            audio = resample_poly(audio, 16000 // d, sr // d)
        return np.clip(audio, -32768, 32767).astype(np.int16)
    except Exception:  # noqa: BLE001
        return None


def mic_from(samples: np.ndarray, lead_s: float = 0.6, tail_s: float = 1.5):
    """A microphone that returns room tone, then the speech, then room tone, then stops."""
    rng = np.random.default_rng(7)
    quiet = lambda n: (rng.standard_normal(n) * 25).astype(np.int16)  # noqa: E731
    stream = np.concatenate([
        quiet(int(lead_s * 16000)), samples, quiet(int(tail_s * 16000)),
    ])
    state = {"i": 0}

    def read():
        i = state["i"]
        if i >= len(stream):
            return None
        state["i"] = i + FRAME
        chunk = stream[i : i + FRAME]
        if len(chunk) < FRAME:
            chunk = np.pad(chunk, (0, FRAME - len(chunk)))
        return chunk.tolist()

    return read


@pytest.fixture(scope="module")
def speech():
    s = piper_speech("Jarvis, what is my battery level right now?")
    if s is None:
        pytest.skip("Piper voice unavailable")
    return s


def test_captures_the_utterance_and_stops_before_the_tail(speech):
    pcm = endpoint.record_utterance(
        mic_from(speech), sample_rate=16000, frame_length=FRAME,
        silence_ms=300, wait_s=3.0,
    )
    assert pcm is not None, "no speech captured from a clearly spoken utterance"
    captured_s = len(pcm) / 2 / 16000
    spoken_s = len(speech) / 16000
    # Should cover the speech plus preroll and hangover, but not the whole 1.5 s of trailing quiet.
    assert captured_s >= spoken_s * 0.85, f"captured only {captured_s:.2f}s of {spoken_s:.2f}s"
    assert captured_s <= spoken_s + 1.2, f"ran {captured_s - spoken_s:.2f}s past the speech"


def test_transcribes_what_was_said(speech):
    from jarvis.audio import local_stt

    pcm = endpoint.record_utterance(
        mic_from(speech), sample_rate=16000, frame_length=FRAME,
        silence_ms=300, wait_s=3.0,
    )
    assert pcm
    text = local_stt.transcribe(pcm, 16000, "base.en", beam_size=1).lower()
    assert "battery" in text, f"transcript missed the key word: {text!r}"


def test_room_tone_alone_produces_nothing(speech):
    rng = np.random.default_rng(3)
    quiet = (rng.standard_normal(16000 * 3) * 25).astype(np.int16)
    pcm = endpoint.record_utterance(
        mic_from(quiet, lead_s=0.0, tail_s=0.0), sample_rate=16000, frame_length=FRAME,
        silence_ms=300, wait_s=1.5,
    )
    assert pcm is None, "silence was treated as an utterance"


def test_partials_arrive_while_speaking(speech):
    seen = []
    endpoint.record_utterance(
        mic_from(speech), sample_rate=16000, frame_length=FRAME,
        silence_ms=300, wait_s=3.0,
        on_partial=lambda p: seen.append(len(p)), partial_every_ms=400,
    )
    assert seen, "no partial audio was offered during a multi-second utterance"
    assert seen == sorted(seen), "partial audio must only grow"


def test_levels_are_published_for_the_hud(speech):
    got = []
    endpoint.record_utterance(
        mic_from(speech), sample_rate=16000, frame_length=FRAME,
        silence_ms=300, wait_s=3.0,
        on_level=lambda lvl, prob: got.append((lvl, prob)),
    )
    assert got, "no audio levels were published"
    assert max(l for l, _ in got) > 0.05, "levels never rose above room tone"
    assert max(p for _, p in got) > 0.5, "speech probability never indicated speech"
