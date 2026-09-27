"""Comparing the model with the reference: real measurements, not impressions.

The model's geometry comes from Blender (``snapshot`` — the evaluated triangles, modifiers and
booleans included). It is rasterised here, in numpy, from the reference camera: GPU-free, so
validation never competes with the live voice service for VRAM, and deterministic.

Two ways to line a render up with a reference:

* ``measured`` — the reference has a known scale (a dimensioned drawing, a stated width). The
  render uses the same millimetres per pixel and is anchored at the bottom-left corner, so a
  part that is 3 mm too long shows up as 3 mm too long.
* ``shape`` — no absolute scale is known (one screenshot). The render is scaled to the
  reference's height; only the shape is compared, and that is what the result says.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import imaging
from .types import CameraEstimate, ValidationResult

MM = 1000.0     # metres → millimetres


# ----------------------------------------------------------------------------- projection
def project(tris_m: np.ndarray, view: str, camera: Optional[CameraEstimate] = None,
            center_mm: Optional[np.ndarray] = None) -> tuple[np.ndarray, np.ndarray]:
    """World triangles (metres) → image-plane coordinates in mm (x right, y up) and depth.

    Orthographic for front/side/top/back; a pinhole camera for "perspective" (image-plane mm
    at unit focal length scaled by the focal length, so the silhouette is in consistent units).
    """
    p = tris_m.reshape(-1, 3).astype(np.float64) * MM
    if view == "front":
        u, v, d = p[:, 0], p[:, 2], p[:, 1]
    elif view == "back":
        u, v, d = -p[:, 0], p[:, 2], -p[:, 1]
    elif view == "side":
        u, v, d = p[:, 1], p[:, 2], -p[:, 0]
    elif view == "top":
        u, v, d = p[:, 0], p[:, 1], -p[:, 2]
    elif view.startswith("orbit:"):
        # Orthographic, the object turned by t about Z (turntable convention of multiview.carve).
        t = math.radians(float(view.split(":", 1)[1]))
        u = p[:, 0] * math.cos(t) - p[:, 1] * math.sin(t)
        v = p[:, 2]
        d = p[:, 0] * math.sin(t) + p[:, 1] * math.cos(t)
    elif view == "perspective":
        cam = camera or CameraEstimate(kind="perspective")
        c = center_mm if center_mm is not None else p.mean(axis=0)
        el, az = math.radians(cam.elevation_deg), math.radians(cam.azimuth_deg)
        dist = cam.distance_mm or 4 * float(np.ptp(p, axis=0).max() or 100)
        pos = c + dist * np.array([math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)])
        fwd = (c - pos) / np.linalg.norm(c - pos)
        right = np.cross(fwd, [0, 0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        rel = p - pos
        depth = rel @ fwd
        depth = np.maximum(depth, 1e-6)
        f = cam.focal_mm / cam.sensor_mm * dist       # keeps the image in roughly object mm
        u, v, d = (rel @ right) / depth * f, (rel @ up) / depth * f, depth
    else:
        raise ValueError("unknown view")
    return np.stack([u, v], axis=1).reshape(-1, 3, 2), d.reshape(-1, 3).mean(axis=1)


def rasterize(uv_mm: np.ndarray, depth: np.ndarray, *, mm_per_px: float, origin_mm: tuple[float, float],
              shape: tuple[int, int], want_depth: bool = False):
    """Fill triangles into a mask (and a painter's-algorithm depth map)."""
    import cv2

    h, w = shape
    ox, oy = origin_mm
    px = np.empty_like(uv_mm)
    px[..., 0] = (uv_mm[..., 0] - ox) / mm_per_px
    px[..., 1] = h - (uv_mm[..., 1] - oy) / mm_per_px
    pts = np.round(px * 16).astype(np.int32)           # 4 bits of sub-pixel precision
    mask = np.zeros((h, w), np.uint8)
    if want_depth:
        dmap = np.full((h, w), np.inf, np.float32)
        order = np.argsort(-depth)
        for i in order:
            tmp = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(tmp, pts[i], 1, lineType=cv2.LINE_8, shift=4)
            sel = tmp.astype(bool)
            dmap[sel] = depth[i]
            mask |= tmp
        return mask.astype(bool), dmap
    # One triangle at a time: fillPoly over many polygons is a single even-odd scanline, where
    # overlapping triangles cancel each other out.
    fill = cv2.fillConvexPoly
    for tri in pts:
        fill(mask, tri, 1, cv2.LINE_8, 4)
    return mask.astype(bool), None


def model_silhouette(tris_m: np.ndarray, view: str, *, mm_per_px: float, pad_px: int = 0,
                     camera: Optional[CameraEstimate] = None, want_depth: bool = False):
    """The model's silhouette, tightly framed (plus padding), and its extent in mm."""
    uv, d = project(tris_m, view, camera)
    flat = uv.reshape(-1, 2)
    lo, hi = flat.min(axis=0), flat.max(axis=0)
    w = int(math.ceil((hi[0] - lo[0]) / mm_per_px)) + 2 * pad_px
    h = int(math.ceil((hi[1] - lo[1]) / mm_per_px)) + 2 * pad_px
    origin = (lo[0] - pad_px * mm_per_px, lo[1] - pad_px * mm_per_px)
    # Supersampled 4×, then a pixel is "in" when at least half of it is covered — how the
    # reference's own pixels came to be, so the comparison is not dominated by rounding.
    k = 1 if want_depth else 4
    mask, dmap = rasterize(uv, d, mm_per_px=mm_per_px / k, origin_mm=origin,
                           shape=(max(h, 1) * k, max(w, 1) * k), want_depth=want_depth)
    if k > 1:
        mask = mask.reshape(max(h, 1), k, max(w, 1), k).mean(axis=(1, 3)) >= 0.5
    return mask, dmap, (float(hi[0] - lo[0]), float(hi[1] - lo[1]))


# ----------------------------------------------------------------------------- comparison
def _crop(mask: np.ndarray) -> np.ndarray:
    b = imaging.bbox(mask)
    if b is None:
        return mask[:0, :0]
    return mask[b[1]:b[3], b[0]:b[2]]


def _landmarks(mask: np.ndarray) -> np.ndarray:
    """Extreme points and centroid, as a small set of comparable landmarks (x, y from bottom-left)."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return np.zeros((0, 2))
    h = mask.shape[0]
    pts = [(xs.min(), ys[xs.argmin()]), (xs.max(), ys[xs.argmax()]), (xs[ys.argmin()], ys.min()),
           (xs[ys.argmax()], ys.max()), (xs.mean(), ys.mean())]
    return np.array([(x, h - y) for x, y in pts], dtype=np.float64)


def _paste(canvas_shape, mask, offset=(0, 0)) -> np.ndarray:
    out = np.zeros(canvas_shape, bool)
    h = min(mask.shape[0], canvas_shape[0] - offset[0])
    w = min(mask.shape[1], canvas_shape[1] - offset[1])
    out[offset[0]:offset[0] + h, offset[1]:offset[1] + w] = mask[:h, :w]
    return out


@dataclass
class Comparison:
    result: ValidationResult
    ref: np.ndarray
    render: np.ndarray
    diff_rows: np.ndarray = field(default_factory=lambda: np.zeros(0))   # per-row width error (px), for refinement


def compare(ref_mask: np.ndarray, render_mask: np.ndarray, *, view: str, align: str = "measured",
            mm_per_px: float = 0.0, expected_mm: Optional[tuple[float, float]] = None,
            model_mm: Optional[tuple[float, float]] = None) -> Comparison:
    """Line the render up with the reference (bottom-left anchored) and measure the difference."""
    import cv2

    ref = _crop(ref_mask)
    ren = _crop(render_mask)
    if ren.size == 0 or ref.size == 0:
        r = ValidationResult(reference_view=view, uncertainty=["nothing to compare"], confidence=0.0)
        return Comparison(r, ref, ren)
    if align == "shape":
        scale = ref.shape[0] / ren.shape[0]
        ren = cv2.resize(ren.astype(np.uint8), (max(1, round(ren.shape[1] * scale)), ref.shape[0]),
                         interpolation=cv2.INTER_NEAREST).astype(bool)
    H = max(ref.shape[0], ren.shape[0])
    W = max(ref.shape[1], ren.shape[1])
    # Anchor bottom-left: both sit on the same ground line and start at the same left edge.
    a = _paste((H, W), ref, (H - ref.shape[0], 0))
    b = _paste((H, W), ren, (H - ren.shape[0], 0))
    # A margin, so an edge on the canvas border is still an edge (erosion ignores the border).
    a, b = np.pad(a, 3), np.pad(b, 3)
    if align == "shape":
        # centre horizontally instead: without scale, the left edge carries no information
        shift = (W - ren.shape[1]) // 2 - (W - ref.shape[1]) // 2
        b = np.roll(b, shift, axis=1)
    iou = imaging.iou(a, b)
    mean_d, p95 = imaging.contour_distance(a, b)
    la, lb = _landmarks(a), _landmarks(b)
    lm = float(np.linalg.norm(la - lb, axis=1).mean()) if len(la) and len(lb) else 0.0
    ea = a.astype(np.uint8) - cv2.erode(a.astype(np.uint8), np.ones((3, 3), np.uint8))
    eb = b.astype(np.uint8) - cv2.erode(b.astype(np.uint8), np.ones((3, 3), np.uint8))
    near = cv2.dilate(eb, np.ones((5, 5), np.uint8))
    edge_mismatch = float(1.0 - (np.logical_and(ea, near).sum() / max(1, ea.sum())))
    prop_ref = ref.shape[1] / max(1, ref.shape[0])
    prop_ren = ren.shape[1] / max(1, ren.shape[0])
    prop = abs(prop_ren - prop_ref) / prop_ref
    dim_err = 0.0
    if expected_mm and model_mm:
        dim_err = max(abs(expected_mm[0] - model_mm[0]), abs(expected_mm[1] - model_mm[1]))
    rows = a.sum(axis=1).astype(np.float64) - b.sum(axis=1).astype(np.float64)
    conf = max(0.0, min(1.0, iou * (1 - min(1.0, edge_mismatch))))
    r = ValidationResult(reference_view=view, silhouette_iou=round(iou, 4), contour_distance_px=round(mean_d, 3),
                         landmark_error_px=round(lm, 3), edge_mismatch=round(edge_mismatch, 4),
                         proportion_error=round(prop, 4), dimension_error_mm=round(dim_err, 3),
                         confidence=round(conf, 3))
    if align == "shape":
        r.uncertainty.append("no absolute scale: only the shape was compared")
    return Comparison(r, a, b, rows)


def color_difference(ref_rgb: np.ndarray, ref_labels: np.ndarray, palette: np.ndarray, material_colors: list) -> float:
    """Mean ΔE between each reference colour layer and the material assigned to it."""
    if not material_colors:
        return 0.0
    diffs = []
    for i, mc in enumerate(material_colors[: len(palette)]):
        diffs.append(imaging.delta_e(palette[i], [int(round(c * 255)) for c in mc]))
    return float(np.mean(diffs)) if diffs else 0.0


# ----------------------------------------------------------------------------- loop control
@dataclass
class LoopPolicy:
    target_iou: float = 0.97
    target_dim_mm: float = 0.5
    min_gain: float = 0.002
    patience: int = 2
    max_iterations: int = 8


def stop_reason(history: list[float], policy: LoopPolicy, *, dim_error_mm: float = 0.0,
                blocked_by_evidence: bool = False) -> str:
    """Why to stop now, or "" to keep going. ``history`` is the worst-view IoU per iteration."""
    if blocked_by_evidence:
        return "missing evidence prevents further improvement"
    if history and history[-1] >= policy.target_iou and dim_error_mm <= policy.target_dim_mm:
        return "target tolerance reached"
    if len(history) >= policy.max_iterations:
        return "iteration budget reached"
    if len(history) > policy.patience:
        recent = history[-(policy.patience + 1):]
        if max(recent[1:]) - recent[0] < policy.min_gain:
            return "improvement plateaued"
    return ""


def worst(results: list[ValidationResult]) -> ValidationResult:
    """The view that matches least — a good front must not hide a bad side."""
    return min(results, key=lambda r: r.silhouette_iou)
