"""Acoustic FMCW: pulse compression, self-calibrating origin, CFAR, range-only output."""
import numpy as np
import pytest

from jarvis.presence import acoustic as a


def synth_echo(delays_samples, amps, length=2 * a.PERIOD_N, noise=0.0, seed=0):
    """Build a received block: direct path plus echoes at given sample delays."""
    rng = np.random.default_rng(seed)
    chirp = a.build_chirp()
    rx = rng.normal(0, noise, length) if noise else np.zeros(length)
    for d, amp in zip(delays_samples, amps):
        end = min(length, d + len(chirp))
        if end > d:
            rx[d:end] += amp * chirp[:end - d]
    return rx


def test_chirp_is_in_the_ultrasonic_band():
    chirp = a.build_chirp()
    spec = np.abs(np.fft.rfft(chirp))
    freqs = np.fft.rfftfreq(len(chirp), 1 / a.SR)
    peak = freqs[int(np.argmax(spec))]
    assert a.F0 - 500 <= peak <= a.F1 + 500
    below = spec[freqs < 15000].max()
    assert below < 0.25 * spec.max()          # little audible energy
    assert abs(chirp[0]) < 1e-6 and abs(chirp[-1]) < 1e-6   # tapered, so no click


def test_compression_finds_the_direct_path():
    rx = synth_echo([300], [1.0])
    profile = a.compress(rx, a.build_chirp())
    assert a.direct_path_index(profile) == pytest.approx(300, abs=2)


def test_origin_is_none_without_a_real_peak():
    assert a.direct_path_index(np.zeros(0)) is None
    rng = np.random.default_rng(1)
    assert a.direct_path_index(np.abs(rng.normal(0, 1, 4000))) is None   # noise only


def test_latency_cancels_out():
    """The same target must give the same range whatever the unknown playback latency.

    This is the defect that made the previous implementation's ranges meaningless: it
    de-chirped against the chirp it had just queued, which is not the one that came back.
    """
    target_extra = int(round(2.0 * 2 / a.C_AIR * a.SR))     # 2.0 m round trip
    seen = []
    for latency in (137, 900, 1800, 3300):                  # arbitrary, incl. > one period
        rx = synth_echo([latency, latency + target_extra], [1.0, 0.25])
        profile = a.compress(rx, a.build_chirp())
        origin = a.direct_path_index(profile)
        assert origin is not None, f"no direct path at latency {latency}"
        ranges = a.range_axis(len(profile), origin)
        seen.append(ranges[origin + target_extra])
    assert all(r == pytest.approx(2.0, abs=0.05) for r in seen)
    assert max(seen) - min(seen) < 0.02                     # and they agree with each other


def test_range_axis_is_round_trip_halved():
    axis = a.range_axis(1000, 0)
    assert axis[0] == 0.0
    one_metre = int(round(1.0 * 2 / a.C_AIR * a.SR))
    assert axis[one_metre] == pytest.approx(1.0, abs=0.01)


def test_cfar_finds_a_peak_above_local_noise_only():
    rng = np.random.default_rng(3)
    profile = np.abs(rng.normal(0, 1, 900)) * 0.1
    profile[400] = 6.0
    peaks = a.cfar(profile)
    assert 400 in peaks
    assert len(peaks) <= 3                                   # not firing on noise everywhere


def test_cfar_is_quiet_on_pure_noise():
    rng = np.random.default_rng(4)
    assert len(a.cfar(np.abs(rng.normal(0, 1, 1500)) * 0.1)) <= 2


def test_targets_are_range_only_and_never_invent_a_bearing():
    sensor = a.AcousticSensor()
    residual = np.zeros(6000)
    origin = 100
    at_1m = origin + int(round(1.0 * 2 / a.C_AIR * a.SR))
    residual[at_1m] = 5.0
    contacts = sensor._targets(residual, origin)
    assert contacts, "expected a detection"
    c = contacts[0]
    assert c.bearing_deg is None                # the whole point
    assert c.position() is None
    assert c.to_dict()["bearing_known"] is False
    assert c.moving is True and c.source == "acoustic"
    assert c.distance_m == pytest.approx(1.0, abs=0.05)


def test_targets_outside_the_usable_window_are_dropped():
    sensor = a.AcousticSensor()
    residual = np.zeros(9000)
    origin = 50
    residual[origin + 4] = 9.0                                     # 1.4 cm: direct-path mainlobe
    residual[origin + int(8.0 * 2 / a.C_AIR * a.SR)] = 9.0         # 8 m: beyond range
    assert sensor._targets(residual, origin) == []


def test_guard_interval_prevents_range_wrap():
    """The listening window must be long enough that MAX_RANGE cannot alias."""
    listen_s = a.PERIOD_S - a.CHIRP_S
    unambiguous_m = listen_s * a.C_AIR / 2
    assert unambiguous_m > a.MAX_RANGE_M
