"""Logos, icons, line art and silhouettes → extruded, bevelled, still-editable curves.

1. Segment the foreground from the backdrop and split it into flat colour layers.
2. Trace every layer's contours with their holes (OpenCV RETR_CCOMP).
3. Simplify each contour to within a fraction of a pixel — the outline stays the reference's.
4. Scale to millimetres: the user's stated width when given (constrained), otherwise a default
   size (estimated) — a picture has no scale of its own.
5. Each colour layer becomes one named curve part, extruded and bevelled by modifiers, so the
   outline, depth and bevel stay editable in Blender.

Evidence is recorded per aspect: the outline is *verified* against the pixels, the size is
*constrained* or *estimated*, and the thickness is *invented* unless the user said it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import imaging
from .types import Evidence, Fidelity, Material, ModelPart, StudioModel

DEFAULT_WIDTH_MM = 100.0
MAX_LAYERS = 6


@dataclass
class VectorOptions:
    width_mm: Optional[float] = None       # user-stated overall width
    height_mm: Optional[float] = None
    depth_mm: Optional[float] = None       # user-stated thickness
    bevel_mm: Optional[float] = None
    epsilon_px: float = 0.6                # contour tolerance: sub-pixel-ish
    min_area_px: int = 12


def _color_name(rgb) -> str:
    r, g, b = [int(x) for x in rgb]
    names = {"Black": (20, 20, 20), "White": (240, 240, 240), "Red": (210, 40, 40), "Green": (40, 160, 70),
             "Blue": (40, 80, 200), "Yellow": (235, 200, 40), "Orange": (240, 130, 30), "Purple": (130, 60, 170),
             "Grey": (128, 128, 128), "Gold": (200, 160, 60), "Teal": (30, 150, 150), "Pink": (230, 120, 170)}
    return min(names, key=lambda n: sum((a - c) ** 2 for a, c in zip((r, g, b), names[n])))


UPSAMPLE = 4


def trace_layer(mask: np.ndarray, epsilon_px: float, min_area_px: int) -> list[dict]:
    """Polygons with holes, in continuous image coordinates (pixel edges at integers).

    Contours are traced on a 4× nearest-neighbour upsampling: OpenCV's contour runs through the
    centres of boundary pixels, which on the original grid would put every outline half a pixel
    inside the true edge. Upsampled, the error is an eighth of a pixel.
    """
    import cv2

    k = UPSAMPLE
    up = np.kron(mask.astype(np.uint8), np.ones((k, k), np.uint8))
    contours, hier = cv2.findContours(up, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None:
        return []
    hier = hier[0]

    def simplify(c):
        pts = cv2.approxPolyDP(c, epsilon_px * k, True).reshape(-1, 2).astype(np.float64)
        return (pts + 0.5) / k

    polys = []
    for i, c in enumerate(contours):
        if hier[i][3] != -1:                  # a hole; attached to its parent below
            continue
        if cv2.contourArea(c) < min_area_px * k * k:
            continue
        outer = simplify(c)
        holes = []
        child = hier[i][2]
        while child != -1:
            hc = contours[child]
            if cv2.contourArea(hc) >= min_area_px * k * k:
                holes.append(simplify(hc))
            child = hier[child][0]
        if len(outer) >= 3:
            polys.append({"outer": outer, "holes": [h for h in holes if len(h) >= 3]})
    return polys


def _to_mm(poly: np.ndarray, cx: float, cy: float, s: float) -> list:
    # Continuous coordinates, centred on the object, y flipped (images count downwards).
    return [[round((float(x) - cx) * s, 4), round((cy - float(y)) * s, 4)] for x, y in poly]


def reconstruct(ref_path: str, name: str = "Logo", opts: Optional[VectorOptions] = None) -> tuple[StudioModel, dict]:
    """Build the model. Returns (model, facts) where facts carries the masks for validation."""
    opts = opts or VectorOptions()
    rgb = imaging.load_rgb(ref_path, 2048)
    mask = imaging.foreground_mask(rgb)
    if mask.sum() < 50:
        raise ValueError("no foreground found in the reference")
    b = imaging.bbox(mask)
    x0, y0, x1, y1 = b
    w_px, h_px = x1 - x0, y1 - y0
    if opts.width_mm:
        s, scale_ev = opts.width_mm / w_px, Evidence.CONSTRAINED
    elif opts.height_mm:
        s, scale_ev = opts.height_mm / h_px, Evidence.CONSTRAINED
    else:
        s, scale_ev = DEFAULT_WIDTH_MM / w_px, Evidence.ESTIMATED
    width_mm, height_mm = w_px * s, h_px * s
    depth = opts.depth_mm or round(max(2.0, min(width_mm, height_mm) * 0.08), 2)
    depth_ev = Evidence.CONSTRAINED if opts.depth_mm else Evidence.INVENTED
    bevel = opts.bevel_mm if opts.bevel_mm is not None else round(min(depth * 0.15, 1.0), 3)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    pal = imaging.quantize(rgb, mask, k=MAX_LAYERS)
    parts, materials = [], []
    used = set()
    kept = np.zeros(mask.shape, bool)
    dropped = 0
    for idx, color in enumerate(pal.colors):
        if pal.shares[idx] < 0.004:
            dropped += int((pal.labels == idx).sum())
            continue
        layer = pal.labels == idx
        polys_px = trace_layer(layer, opts.epsilon_px, opts.min_area_px)
        if not polys_px:
            continue
        kept |= layer
        cname = _color_name(color)
        pname = f"{name} {cname}" if cname not in used else f"{name} {cname} {idx + 1}"
        used.add(cname)
        polys = [{"outer": _to_mm(p["outer"], cx, cy, s), "holes": [_to_mm(h, cx, cy, s) for h in p["holes"]]}
                 for p in polys_px]
        mat = Material(name=f"{pname} material", color=[round(c / 255.0, 4) for c in color], roughness=0.4,
                       evidence=Evidence.VERIFIED)
        materials.append(mat)
        aspects = {"outline": Evidence.VERIFIED.value, "scale": scale_ev.value, "depth": depth_ev.value,
                   "colour": Evidence.VERIFIED.value}
        parts.append(ModelPart(
            name=pname, geometry="curve_extrude",
            params={"polygons": polys, "depth": depth, "bevel": bevel, "width": width_mm, "height": height_mm,
                    "evidence": aspects, "points": int(sum(len(p["outer"]) + sum(len(h) for h in p["holes"])
                                                           for p in polys))},
            location=[0.0, 0.0, round(height_mm / 2, 4)], rotation=[90.0, 0.0, 0.0],
            material=mat.name, evidence=Evidence.weakest(aspects.values()),
            confidence=0.9 if scale_ev == Evidence.CONSTRAINED else 0.6, aliases=[cname.lower(), "logo", "layer"]))
    model = StudioModel(name=name, parts=parts, materials=materials, category="logo",
                        fidelity=Fidelity.VISUAL if depth_ev == Evidence.INVENTED else Fidelity.DIMENSIONAL)
    # Validation compares against what was modelled; specks too small to model are listed, not hidden.
    kb = imaging.bbox(kept) or b
    facts = {"mask": kept[kb[1]:kb[3], kb[0]:kb[2]], "scale_mm_per_px": s, "bbox": b, "colors": len(parts),
             "width_mm": width_mm, "height_mm": height_mm, "dropped_px": dropped}
    return model, facts
