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
                              np.zeros((480, width, 3), np.uint8), width, 480)
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
                          np.zeros((480, width, 3), np.uint8), width, 480)[0]
    right = sensor._detect(FakeDetector([face_row(520, 240, 100, 120, ipd_px)]),
                           np.zeros((480, width, 3), np.uint8), width, 480)[0]
    assert left.bearing_deg < -10 and right.bearing_deg > 10
    assert left.position()[0] < 0 < right.position()[0]


def test_two_faces_become_two_contacts_with_distinct_ids():
    width = 640
    f = geo.focal_px(width)
    sensor = camera.CameraSensor()
    contacts = sensor._detect(FakeDetector([
        face_row(180, 240, 100, 120, f * geo.IPD_M / 1.2),
        face_row(460, 240, 80, 96, f * geo.IPD_M / 2.6),
    ]), np.zeros((480, width, 3), np.uint8), width, 480)
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
                              np.zeros((480, width, 3), np.uint8), width, 480)
    assert contacts == []


def test_no_faces_is_empty_not_an_error():
    sensor = camera.CameraSensor()
    assert sensor._detect(FakeDetector([]), np.zeros((480, 640, 3), np.uint8), 640, 480) == []


def test_dark_threshold_is_configured():
    assert 0 < camera.DARK_LEVEL < 40


class _FakeCap:
    """Mimics cv2.VideoCapture: some /dev/videoN nodes open but never yield a frame."""

    def __init__(self, index, yields):
        self.index, self._yields, self.released = index, yields, False

    def isOpened(self): return self.index in (1, 2)
    def set(self, *_): return True
    def read(self):
        return (True, np.zeros((480, 640, 3), np.uint8)) if self._yields else (False, None)
    def get(self, _prop): return 640
    def release(self): self.released = True


def test_candidate_indices_prefers_an_explicit_override(monkeypatch):
    monkeypatch.setattr(camera, "_DEVICE", 3)
    assert camera.candidate_indices() == [3]


def test_candidate_indices_enumerates_device_nodes(monkeypatch):
    monkeypatch.setattr(camera, "_DEVICE", None)
    monkeypatch.setattr(camera.glob, "glob", lambda _p: ["/dev/video2", "/dev/video1", "/dev/videoX"])
    assert camera.candidate_indices() == [1, 2]      # sorted, non-numeric ignored


def test_open_capture_skips_a_node_that_never_delivers_a_frame(monkeypatch):
    """video1 opens but yields nothing (a metadata node); video2 is the real capture."""
    monkeypatch.setattr(camera, "_DEVICE", None)
    monkeypatch.setattr(camera, "candidate_indices", lambda: [1, 2])
    made = []

    class FakeCv2:
        @staticmethod
        def VideoCapture(index):
            cap = _FakeCap(index, yields=(index == 2))
            made.append(cap)
            return cap
        CAP_PROP_FRAME_WIDTH = CAP_PROP_FRAME_HEIGHT = 0

    cap, index = camera.open_capture(FakeCv2)
    assert index == 2 and cap is not None
    assert made[0].released is True, "the dead node must be released, not leaked"


def test_open_capture_returns_none_when_nothing_works(monkeypatch):
    monkeypatch.setattr(camera, "candidate_indices", lambda: [7])

    class FakeCv2:
        @staticmethod
        def VideoCapture(index): return _FakeCap(99, yields=False)
        CAP_PROP_FRAME_WIDTH = CAP_PROP_FRAME_HEIGHT = 0

    assert camera.open_capture(FakeCv2) == (None, None)


def test_person_box_with_a_face_gets_metric_range(monkeypatch):
    """A YOLOX body with a YuNet face inside it -> one contact carrying IPD distance."""
    width, height = 640, 480
    f = geo.focal_px(width)
    truth = 1.6
    ipd_px = f * geo.IPD_M / truth
    box = {"x": 260, "y": 60, "w": 120, "h": 300, "score": 0.85}   # body, legs off bottom
    monkeypatch.setattr(camera.person_det, "detect", lambda *a: [box])
    sensor = camera.CameraSensor()
    sensor._person_session = object()                              # truthy -> person path on
    faces = FakeDetector([face_row(width / 2, 120, 80, 90, ipd_px)])
    contacts = sensor._detect(faces, np.zeros((height, width, 3), np.uint8), width, height)
    assert len(contacts) == 1
    c = contacts[0]
    assert c.distance_m == pytest.approx(truth, rel=0.03)
    assert "+face(ipd)" in c.detail and c.bearing_deg == pytest.approx(0.0, abs=2)


def test_person_box_without_a_face_is_bearing_only(monkeypatch):
    """No face visible (turned away): presence + bearing, honest about unknown range."""
    width, height = 640, 480
    box = {"x": 40, "y": 60, "w": 120, "h": 430, "score": 0.8}    # left side, legs off bottom edge
    monkeypatch.setattr(camera.person_det, "detect", lambda *a: [box])
    sensor = camera.CameraSensor()
    sensor._person_session = object()
    contacts = sensor._detect(FakeDetector([]), np.zeros((height, width, 3), np.uint8), width, height)
    assert len(contacts) == 1
    c = contacts[0]
    assert c.distance_m is None and c.bearing_deg < -10       # to the left, range unknown
    assert c.confidence >= 0.5 and c.detail.startswith("person")


def test_two_bodies_become_two_contacts(monkeypatch):
    width, height = 640, 480
    boxes = [{"x": 60, "y": 50, "w": 110, "h": 300, "score": 0.8},
             {"x": 440, "y": 50, "w": 110, "h": 300, "score": 0.75}]
    monkeypatch.setattr(camera.person_det, "detect", lambda *a: boxes)
    sensor = camera.CameraSensor()
    sensor._person_session = object()
    contacts = sensor._detect(FakeDetector([]), np.zeros((height, width, 3), np.uint8), width, height)
    assert len(contacts) == 2
    assert contacts[0].bearing_deg < 0 < contacts[1].bearing_deg


def test_close_up_face_with_no_body_still_reported(monkeypatch):
    """Head fills the frame, YOLOX misses the body — the face must not be lost."""
    width, height = 640, 480
    f = geo.focal_px(width)
    monkeypatch.setattr(camera.person_det, "detect", lambda *a: [])   # no body
    sensor = camera.CameraSensor()
    sensor._person_session = object()
    faces = FakeDetector([face_row(width / 2, 240, 200, 240, f * geo.IPD_M / 0.6)])
    contacts = sensor._detect(faces, np.zeros((height, width, 3), np.uint8), width, height)
    assert len(contacts) == 1 and contacts[0].distance_m == pytest.approx(0.6, rel=0.05)
