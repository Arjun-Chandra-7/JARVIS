"""Full-body person detection — the robust core of the camera sensor.

Face-only detection failed the moment the webcam was aimed a little high, the light was
poor, or someone turned their head. A whole-body detector (YOLOX-S, COCO class "person")
finds a person from head, torso or legs, so it keeps working when no face is visible. It
gives presence, a horizontal bearing and a head count reliably; metric distance is then
refined by the face/IPD path in camera.py whenever a face happens to be visible.

Pure functions here (letterbox, decode, nms, geometry) so the maths is unit-tested
without a camera. The model is the Apache-licensed YOLOX-S from the same opencv_zoo
repository as the YuNet face model.
"""
from __future__ import annotations

import math
import os
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np

from . import geometry as geo

INPUT_SIZE = 640
PERSON_CLASS = 0                    # COCO class index for "person"
MODEL_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "object_detection_yolox/object_detection_yolox_2022nov.onnx")
MODEL_PATH = Path(os.environ.get(
    "JARVIS_YOLOX_MODEL", "~/.local/share/jarvis/models/yolox.onnx")).expanduser()

SCORE_THRESHOLD = float(os.environ.get("JARVIS_PERSON_SCORE", "0.45"))
NMS_THRESHOLD = float(os.environ.get("JARVIS_PERSON_NMS", "0.45"))
STANDING_HEIGHT_M = 1.70            # mean adult stature, used only when the whole body is in frame


def ensure_model(timeout: float = 120.0) -> bool:
    """Fetch the ~35 MB YOLOX model once. Returns True when it is on disk."""
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 1_000_000:
        return True
    try:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_PATH.with_suffix(".tmp")
        with urllib.request.urlopen(MODEL_URL, timeout=timeout) as r, tmp.open("wb") as f:
            f.write(r.read())
        if tmp.stat().st_size < 1_000_000:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(MODEL_PATH)
        return True
    except Exception:  # noqa: BLE001 - offline is a normal, non-fatal state
        return False


def letterbox(frame: np.ndarray) -> tuple[np.ndarray, float]:
    """Aspect-preserving resize to INPUT_SIZE, padded with YOLOX's 114 grey. Returns (blob, ratio)."""
    h, w = frame.shape[:2]
    ratio = min(INPUT_SIZE / h, INPUT_SIZE / w)
    nh, nw = int(round(h * ratio)), int(round(w * ratio))
    padded = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114.0, dtype=np.float32)
    import cv2
    padded[:nh, :nw] = cv2.resize(frame, (nw, nh)).astype(np.float32)
    blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None])   # NCHW
    return blob, ratio


def _grids_and_strides() -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for stride in (8, 16, 32):
        g = INPUT_SIZE // stride
        yv, xv = np.meshgrid(np.arange(g), np.arange(g), indexing="ij")
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((g * g, 1), stride))
    return np.concatenate(grids, 0), np.concatenate(strides, 0)


_GRID, _STRIDE = _grids_and_strides()


def decode_persons(raw: np.ndarray, ratio: float,
                   score_threshold: float = SCORE_THRESHOLD) -> list[dict]:
    """Decode YOLOX output [8400,85] to person boxes in ORIGINAL-frame pixels.

    Boxes are returned as {x, y, w, h, score} with x,y the top-left corner. Coordinates
    are mapped back through the letterbox ratio so they line up with the source frame.
    """
    out = np.asarray(raw, dtype=np.float32).reshape(-1, 85).copy()
    out[:, :2] = (out[:, :2] + _GRID) * _STRIDE          # centre xy
    out[:, 2:4] = np.exp(out[:, 2:4]) * _STRIDE          # wh
    obj = out[:, 4]
    person_prob = out[:, 5 + PERSON_CLASS]
    best = out[:, 5:].argmax(1)
    scores = obj * person_prob
    keep = (best == PERSON_CLASS) & (scores >= score_threshold)
    dets = []
    for cx, cy, bw, bh, score in zip(out[keep, 0], out[keep, 1], out[keep, 2],
                                     out[keep, 3], scores[keep]):
        x = (cx - bw / 2) / ratio
        y = (cy - bh / 2) / ratio
        dets.append({"x": float(x), "y": float(y), "w": float(bw / ratio),
                     "h": float(bh / ratio), "score": float(score)})
    return dets


def nms(dets: list[dict], iou_threshold: float = NMS_THRESHOLD) -> list[dict]:
    """Greedy non-maximum suppression, highest score first."""
    if not dets:
        return []
    boxes = np.array([[d["x"], d["y"], d["x"] + d["w"], d["y"] + d["h"]] for d in dets])
    scores = np.array([d["score"] for d in dets])
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    order = scores.argsort()[::-1]
    kept = []
    while order.size:
        i = order[0]
        kept.append(dets[i])
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_threshold]
    return kept


def person_bearing(det: dict, frame_width: int) -> float:
    """Horizontal bearing of a person box centre, via the shared pinhole model."""
    return geo.bearing_deg(det["x"] + det["w"] / 2.0, frame_width)


def person_distance(det: dict, frame_height: int, frame_width: int,
                    edge_margin: int = 4) -> Optional[float]:
    """Rough range from box height — only when the whole body is genuinely in frame.

    If the box touches the top or bottom edge the legs (or head) are cut off, so a
    height-based distance would be meaningless; we return None and let the face/IPD
    path supply distance instead.
    """
    top, bottom = det["y"], det["y"] + det["h"]
    if top <= edge_margin or bottom >= frame_height - edge_margin:
        return None
    if det["h"] <= 1:
        return None
    return geo.focal_px(frame_width) * STANDING_HEIGHT_M / det["h"]


def detect(session, frame) -> list[dict]:
    """Run the model on one BGR frame and return NMS-filtered person boxes."""
    blob, ratio = letterbox(frame)
    raw = session.run(None, {session.get_inputs()[0].name: blob})[0][0]
    return nms(decode_persons(raw, ratio))
