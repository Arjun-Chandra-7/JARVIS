"""Camera geometry: bearing and metric range must be right, or the radar lies."""
import math

import pytest

from jarvis.presence import geometry as g


W = 1280


def test_focal_matches_fov_round_trip():
    for fov in (55.0, 68.0, 78.0, 90.0):
        f = g.focal_px(W, fov)
        back = math.degrees(2 * math.atan2(W / 2, f))
        assert back == pytest.approx(fov, abs=1e-9)


def test_bearing_centre_edges_and_sign():
    assert g.bearing_deg(W / 2, W, 68.0) == pytest.approx(0.0, abs=1e-9)
    assert g.bearing_deg(W, W, 68.0) == pytest.approx(34.0, abs=1e-6)      # right edge = +HFOV/2
    assert g.bearing_deg(0, W, 68.0) == pytest.approx(-34.0, abs=1e-6)     # left edge  = -HFOV/2
    assert g.bearing_deg(W * 0.75, W, 68.0) > 0                            # right of centre is positive


def test_bearing_is_pinhole_not_linear():
    """Naively scaling the FOV by pixel fraction gives 17 deg at the quarter point.

    The pinhole model gives 18.6 deg: the two agree only at the centre and the edge,
    and a linear model under-reports every bearing in between.
    """
    pinhole = g.bearing_deg(W * 0.75, W, 68.0)
    linear = 68.0 * 0.25
    assert pinhole == pytest.approx(18.64, abs=0.05)
    assert pinhole > linear + 0.5
    # ...yet they still coincide exactly at the frame edge
    assert g.bearing_deg(W, W, 68.0) == pytest.approx(34.0, abs=1e-6)


def test_distance_from_ipd_is_metric():
    f = g.focal_px(W, 68.0)
    for truth in (0.5, 1.0, 2.0, 4.0):
        ipd_px = f * g.IPD_M / truth            # what the detector would measure
        assert g.distance_from_ipd(ipd_px, W, 68.0) == pytest.approx(truth, rel=1e-9)


def test_distance_shrinks_as_ipd_grows():
    near = g.distance_from_ipd(80, W, 68.0)
    far = g.distance_from_ipd(20, W, 68.0)
    assert far == pytest.approx(near * 4, rel=1e-9)


def test_biological_spread_bounds_the_error():
    """+/-3 mm of IPD spread must map to roughly +/-5% of range, not more."""
    f = g.focal_px(W, 68.0)
    ipd_px = f * g.IPD_M / 2.0                  # a person at exactly 2 m
    lo = f * (g.IPD_M - g.IPD_SIGMA_M) / ipd_px
    hi = f * (g.IPD_M + g.IPD_SIGMA_M) / ipd_px
    assert 1.80 < lo < 1.95 and 2.05 < hi < 2.20


def test_confidence_prefers_ipd_and_decays_with_distance():
    assert g.range_confidence(1.0, True) > g.range_confidence(1.0, False)
    assert g.range_confidence(1.0, True) > g.range_confidence(5.0, True)
    assert 0.0 < g.range_confidence(6.0, False) <= 1.0


def test_calibration_recovers_focal_and_fov():
    f = g.focal_px(W, 68.0)
    ipd_px = f * g.IPD_M / 1.5
    cal = g.Calibration.from_known_distance(ipd_px, 1.5, W)
    assert cal.focal_px == pytest.approx(f, rel=1e-9)
    assert cal.hfov_deg == pytest.approx(68.0, abs=1e-6)


@pytest.mark.parametrize("bad", [0, -5])
def test_invalid_inputs_raise(bad):
    with pytest.raises(ValueError):
        g.distance_from_ipd(bad, W)
    with pytest.raises(ValueError):
        g.focal_px(bad)
