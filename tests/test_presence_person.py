"""Full-body person detection maths: decode, NMS, bearing, range — no camera needed."""
import numpy as np
import pytest

from jarvis.presence import person as pr
from jarvis.presence import geometry as geo


def raw_with_box(cx, cy, bw, bh, obj=0.9, cls=0.9, grid_index=0, stride=8):
    """Craft a YOLOX [8400,85] output whose grid cell decodes to (cx,cy,bw,bh) in 640-space."""
    out = np.zeros((8400, 85), np.float32)
    gx, gy = pr._GRID[grid_index]
    out[grid_index, 0] = cx / stride - gx
    out[grid_index, 1] = cy / stride - gy
    out[grid_index, 2] = np.log(bw / stride)
    out[grid_index, 3] = np.log(bh / stride)
    out[grid_index, 4] = obj
    out[grid_index, 5] = cls          # class 0 = person
    return out


def test_decode_recovers_a_person_box_in_original_pixels():
    # grid cell 0 is stride-8, grid (0,0) -> centre at (cx,cy)*? ; use a known centre
    out = raw_with_box(cx=320, cy=320, bw=80, bh=200)
    dets = pr.decode_persons(out, ratio=1.0)
    assert len(dets) == 1
    d = dets[0]
    assert d["x"] == pytest.approx(320 - 40, abs=1)
    assert d["y"] == pytest.approx(320 - 100, abs=1)
    assert d["w"] == pytest.approx(80, abs=1) and d["h"] == pytest.approx(200, abs=1)
    assert d["score"] == pytest.approx(0.81, abs=0.01)


def test_decode_maps_back_through_the_letterbox_ratio():
    out = raw_with_box(cx=320, cy=320, bw=100, bh=100)
    half = pr.decode_persons(out, ratio=0.5)[0]     # model saw a half-size frame
    assert half["w"] == pytest.approx(200, abs=1)     # so real box is twice as big
    assert half["x"] == pytest.approx((320 - 50) / 0.5, abs=2)


def test_only_person_class_and_above_threshold_survive():
    out = raw_with_box(cx=100, cy=100, bw=40, bh=40, cls=0.9)     # person, strong
    out[1, 0] = 0; out[1, 4] = 0.9; out[1, 5 + 2] = 0.9           # a car (class 2)
    dets = pr.decode_persons(out, ratio=1.0, score_threshold=0.4)
    assert all("score" in d for d in dets) and len(dets) == 1     # the car is dropped


def test_nms_collapses_overlapping_boxes():
    a = {"x": 100, "y": 100, "w": 50, "h": 120, "score": 0.9}
    b = {"x": 104, "y": 103, "w": 50, "h": 120, "score": 0.7}     # ~same person
    far = {"x": 400, "y": 100, "w": 50, "h": 120, "score": 0.8}
    kept = pr.nms([a, b, far])
    assert len(kept) == 2
    assert {round(d["x"]) for d in kept} == {100, 400}
    assert kept[0]["score"] == 0.9                                # highest kept first


def test_bearing_sign_matches_side_of_frame():
    W = 640
    left = {"x": 40, "y": 100, "w": 60, "h": 200}
    right = {"x": 540, "y": 100, "w": 60, "h": 200}
    assert pr.person_bearing(left, W) < -10
    assert pr.person_bearing(right, W) > 10
    centre = {"x": W / 2 - 30, "y": 100, "w": 60, "h": 200}
    assert pr.person_bearing(centre, W) == pytest.approx(0.0, abs=1.0)


def test_distance_from_full_body_box_is_metric():
    W, H = 640, 480
    truth = 3.0
    box_h = geo.focal_px(W) * pr.STANDING_HEIGHT_M / truth        # what the box would measure
    det = {"x": 300, "y": 120, "w": 80, "h": box_h}              # comfortably inside the frame
    assert det["y"] > 4 and det["y"] + box_h < H - 4
    assert pr.person_distance(det, H, W) == pytest.approx(truth, rel=0.02)


def test_distance_is_none_when_the_body_is_cut_off():
    W, H = 640, 480
    touches_bottom = {"x": 300, "y": 100, "w": 80, "h": H}       # legs off the bottom edge
    assert pr.person_distance(touches_bottom, H, W) is None
    touches_top = {"x": 300, "y": 0, "w": 80, "h": 200}          # head off the top edge
    assert pr.person_distance(touches_top, H, W) is None
