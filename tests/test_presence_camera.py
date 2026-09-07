"""Camera sensor: detection -> Contact conversion, without needing a real webcam."""
import numpy as np
import pytest

from jarvis.presence import camera, geometry as geo


class FakeDetector:
    """Stands in for cv2.FaceDetectorYN. Returns YuNet's 15-column face rows."""

    def __init__(self, faces):
        self._faces = faces

    def detect(self, frame):
        return 1, (np.array(self._faces, dtype=np.float32) if self._faces else None)


def face_row(cx, cy, w, h, ipd_px, score=0.95):
    """A YuNet row: box, then right-eye, left-eye, nose, two mouth corners, then score."""
    x, y = cx - w / 2, cy - h / 2
    rex, lex = cx - ipd_px / 2, cx + ipd_px / 2
    ey = cy - h * 0.1
    return [x, y, w, h, rex, ey, lex, ey, cx, cy, cx - w * .2, cy + h * .2, cx + w * .2, cy + h * .2, score]


def test_ipd_from_landmarks():
    assert camera._ipd_px([(100.0, 50.0), (160.0, 50.0)]) == pytest.approx(60.0)
    assert camera._ipd_px([(100.0, 50.0), (100.5, 50.0)]) is None      # degenerate
    assert camera._ipd_px([]) is None


def test_detect_builds_a_located_contact_with_metric_range():
    width = 640
    f = geo.focal_px(width)
    truth_m = 1.5
    ipd_px = f * geo.IPD_M / truth_m
    sensor = camera.CameraSensor()
    contacts = sensor._detect(FakeDetector([face_row(width / 2, 240, 120, 140, ipd_px)]),
                              np.zeros((480, width, 3), np.uint8), width)
    assert len(contacts) == 1
    c = contacts[0]
    assert c.source == "camera"
    assert c.distance_m == pytest.approx(truth_m, rel=0.01)
    assert c.bearing_deg == pytest.approx(0.0, abs=0.01)
    assert c.position() is not None and c.confidence > 0.5
    assert "ipd" in c.detail


def test_bearing_sign_follows_position_in_frame():
    width = 640
    f = geo.focal_px(width)
    ipd_px = f * geo.IPD_M / 2.0
    sensor = camera.CameraSensor()
    left = sensor._detect(FakeDetector([face_row(120, 240, 100, 120, ipd_px)]),
                          np.zeros((480, width, 3), np.uint8), width)[0]
    right = sensor._detect(FakeDetector([face_row(520, 240, 100, 120, ipd_px)]),
                           np.zeros((480, width, 3), np.uint8), width)[0]
    assert left.bearing_deg < -10 and right.bearing_deg > 10
    assert left.position()[0] < 0 < right.position()[0]


def test_two_faces_become_two_contacts_with_distinct_ids():
    width = 640
    f = geo.focal_px(width)
    sensor = camera.CameraSensor()
    contacts = sensor._detect(FakeDetector([
        face_row(180, 240, 100, 120, f * geo.IPD_M / 1.2),
        face_row(460, 240, 80, 96, f * geo.IPD_M / 2.6),
    ]), np.zeros((480, width, 3), np.uint8), width)
    assert len(contacts) == 2
    assert len({c.id for c in contacts}) == 2
    near, far = sorted(contacts, key=lambda c: c.distance_m)
    assert near.distance_m == pytest.approx(1.2, rel=0.02)
    assert far.distance_m == pytest.approx(2.6, rel=0.02)
    assert near.confidence >= far.confidence          # closer range is trusted more


def test_implausible_ranges_are_discarded():
    """A 'face' implying 40 m is a false positive on a laptop webcam, not a person."""
    width = 640
    sensor = camera.CameraSensor()
    contacts = sensor._detect(FakeDetector([face_row(320, 240, 4, 5, 1.5)]),
                              np.zeros((480, width, 3), np.uint8), width)
    assert contacts == []


def test_no_faces_is_empty_not_an_error():
    sensor = camera.CameraSensor()
    assert sensor._detect(FakeDetector([]), np.zeros((480, 640, 3), np.uint8), 640) == []


def test_dark_threshold_is_configured():
    assert 0 < camera.DARK_LEVEL < 40
