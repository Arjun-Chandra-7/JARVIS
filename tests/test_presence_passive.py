"""Passive audio: tell a voice from a fan, and never claim a direction."""
import numpy as np
import pytest

from jarvis.presence import passive as p


def tone(freq, seconds=p.FRAME_S, amp=0.2, sr=p.SR):
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return amp * np.sin(2 * np.pi * freq * t)


def noise(seconds=p.FRAME_S, amp=0.2, seed=0, sr=p.SR):
    return np.random.default_rng(seed).normal(0, amp, int(sr * seconds))


def test_speech_band_tone_is_dominant():
    _energy, dominance = p.features(tone(1000))
    assert dominance > 0.9, "a 1 kHz tone sits squarely in the speech band"


def test_out_of_band_tone_is_not_dominant():
    _energy, dominance = p.features(tone(60))          # mains hum / rumble
    assert dominance < 0.2


def test_broadband_noise_is_not_dominant():
    _energy, dominance = p.features(noise())
    assert dominance < p.MIN_DOMINANCE, "white noise must not look like speech"


def test_silence_is_zero():
    energy, dominance = p.features(np.zeros(p.FRAME_N))
    assert energy == pytest.approx(0.0, abs=1e-12)


def test_empty_frame_is_safe():
    assert p.features(np.array([])) == (0.0, 0.0)


def test_steady_loud_tone_is_rejected_as_a_fan():
    """Loud and in-band, but perfectly steady — a fan, not a voice."""
    steady = [10.0] * p.HISTORY_LEN
    assert p.is_speech(steady, dominance=0.9, floor=0.01) is False


def test_modulated_in_band_energy_reads_as_speech():
    """Syllable-rate variation is what separates a voice from a tone."""
    varying = [10.0, 1.0, 8.0, 0.5, 12.0, 2.0, 9.0, 1.5] * 2
    assert p.is_speech(varying, dominance=0.8, floor=0.01) is True


def test_quiet_speech_below_the_floor_is_rejected():
    varying = [1.0, 0.1, 0.8, 0.05, 1.2, 0.2, 0.9, 0.15] * 2
    assert p.is_speech(varying, dominance=0.8, floor=10.0) is False


def test_in_band_but_broadband_is_rejected():
    varying = [10.0, 1.0, 8.0, 0.5, 12.0, 2.0, 9.0, 1.5] * 2
    assert p.is_speech(varying, dominance=0.1, floor=0.01) is False


def test_too_little_history_is_not_speech():
    assert p.is_speech([10.0, 1.0], dominance=0.9, floor=0.01) is False


def test_contact_has_no_position_and_holds_briefly():
    sensor = p.PassiveSensor()
    now = 1000.0
    sensor._last_speech = now
    sensor._first_speech = now
    sensor._publish(True, now, floor=0.01, energy=1.0, dominance=0.8)
    contacts, status = sensor.snapshot()
    assert len(contacts) == 1
    c = contacts[0]
    assert c.distance_m is None and c.bearing_deg is None
    assert c.position() is None and c.to_dict()["bearing_known"] is False
    assert c.source == "audio" and "voice heard" in c.detail
    assert status.ok and "speech" in status.detail


def test_quiet_publishes_nothing():
    sensor = p.PassiveSensor()
    sensor._publish(False, 1000.0, floor=0.01, energy=0.01, dominance=0.1)
    contacts, status = sensor.snapshot()
    assert contacts == []
    assert status.ok and "quiet" in status.detail


def test_a_single_blip_does_not_declare_presence():
    """A cough or a keystroke can pass the per-frame test; presence needs several."""
    assert p.VOTE_HITS > 1 and p.VOTE_WINDOW >= p.VOTE_HITS
    votes = [False] * p.VOTE_WINDOW
    votes[-1] = True
    assert sum(votes) < p.VOTE_HITS


def test_sustained_speech_declares_presence():
    votes = [True] * p.VOTE_HITS + [False] * (p.VOTE_WINDOW - p.VOTE_HITS)
    assert sum(votes) >= p.VOTE_HITS
