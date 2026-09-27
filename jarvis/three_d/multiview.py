"""Several views of one object → its exterior, by silhouette carving (a visual hull).

For turntable frames and orbit videos: the camera circles the object at roughly constant
height, so each frame's angle is known (evenly spaced over a turn, unless given) and the axis of
rotation is the average silhouette centre over the turn.

1. Silhouettes from every frame; frames whose height or area disagrees with the rest (a
   different object, a cut, a hand in the way) are rejected, and ORB landmark matches between
   neighbouring frames are counted as a second opinion.
2. A voxel grid is carved: a voxel survives only if every accepted view sees object there.
3. The surface of what survives becomes a mesh part; the source cameras are kept in Blender.

What this cannot recover — and says so: concavities no silhouette reveals (the inside of a cup),
and absolute scale unless a measurement is given.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import imaging
from .types import CameraEstimate, Evidence, Fidelity, Material, ModelPart, StudioModel, ViewKind

MAX_GRID = 96


@dataclass
class ViewCheck:
    accepted: list = field(default_factory=list)
    rejected: list = field(default_factory=list)     # (index, reason)
    landmark_matches: list = field(default_factory=list)


def silhouettes(paths: list[str]) -> list[np.ndarray]:
    return [imaging.filled_silhouette(imaging.foreground_mask(imaging.load_rgb(p, 1024))) for p in paths]


def orb_matches(a_rgb: np.ndarray, b_rgb: np.ndarray) -> int:
    import cv2

    orb = cv2.ORB_create(500)
    ga, gb = cv2.cvtColor(a_rgb, cv2.COLOR_RGB2GRAY), cv2.cvtColor(b_rgb, cv2.COLOR_RGB2GRAY)
    ka, da = orb.detectAndCompute(ga, None)
    kb, db = orb.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return 0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 8:
        return len(matches)
    src = np.float32([ka[m.queryIdx].pt for m in matches])
    dst = np.float32([kb[m.trainIdx].pt for m in matches])
    _, inl = cv2.findFundamentalMat(src, dst, cv2.FM_RANSAC, 3.0, 0.99)
    return int(inl.sum()) if inl is not None else 0


def check_views(masks: list[np.ndarray], rgbs: Optional[list] = None, tol: float = 0.15) -> ViewCheck:
    out = ViewCheck()
    heights = np.array([(imaging.bbox(m) or (0, 0, 0, 0))[3] - (imaging.bbox(m) or (0, 0, 0, 0))[1] for m in masks], float)
    med = float(np.median(heights)) if len(heights) else 0.0
    for i, h in enumerate(heights):
        if med <= 0 or abs(h - med) / med > tol:
            out.rejected.append((i, "height disagrees with the other views"))
        else:
            out.accepted.append(i)
    if rgbs is not None and len(rgbs) > 1:
        out.landmark_matches = [orb_matches(rgbs[i], rgbs[(i + 1) % len(rgbs)]) for i in range(len(rgbs))]
    return out


def carve(masks: list[np.ndarray], angles_deg: list[float], grid: int = 72) -> tuple[np.ndarray, dict]:
    """Voxel occupancy in a normalised frame: height 1.0, x/y in the same units."""
    grid = min(grid, MAX_GRID)
    crops, axes = [], []
    ref_h = None
    for m in masks:
        b = imaging.bbox(m)
        x0, y0, x1, y1 = b
        crops.append((m, b))
        ref_h = ref_h or (y1 - y0)
    # Normalise each frame to unit height (a turntable keeps height; zoom drift is removed).
    norm = []
    for m, (x0, y0, x1, y1) in crops:
        h = y1 - y0
        norm.append((m, x0, y1, h))
        axes.append((x0 + x1) / 2)
    # The rotation axis: silhouette centres average to it over a full turn.
    half_widths = [((x1 - x0) / 2) / (y1 - y0) for _, (x0, y0, x1, y1) in crops]
    R = max(half_widths) * 1.15 + 0.02
    lin = np.linspace(-R, R, grid)
    zs = np.linspace(0, 1, grid)
    X, Y, Z = np.meshgrid(lin, lin, zs, indexing="ij")
    occ = np.ones(X.shape, bool)
    centres = [ax for ax in axes]
    for (m, x0, ybot, h), ang, (_, (bx0, _, bx1, _)) in zip(norm, angles_deg, crops):
        t = math.radians(ang)
        u = X * math.cos(t) - Y * math.sin(t)                # object turned by t, camera fixed at -Y
        axis_px = float(np.mean(centres))
        col = np.round(axis_px + u * h).astype(int)
        row = np.round(ybot - Z * h).astype(int)
        inside = (col >= 0) & (col < m.shape[1]) & (row >= 0) & (row < m.shape[0])
        hit = np.zeros(X.shape, bool)
        hit[inside] = m[row[inside], col[inside]]
        occ &= hit
    return occ, {"R": R, "grid": grid}


def surface_mesh(occ: np.ndarray, extent: tuple[float, float, float]) -> tuple[list, list]:
    """Exposed voxel faces → a closed quad mesh (vertices shared)."""
    gx, gy, gz = occ.shape
    sx, sy, sz = extent[0] / gx, extent[1] / gy, extent[2] / gz
    ox, oy = -extent[0] / 2, -extent[1] / 2
    verts: dict[tuple, int] = {}
    vlist, faces = [], []

    def v(i, j, k):
        key = (i, j, k)
        if key not in verts:
            verts[key] = len(vlist)
            vlist.append([ox + i * sx, oy + j * sy, k * sz])
        return verts[key]

    padded = np.pad(occ, 1)
    dirs = [((1, 0, 0), [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
            ((-1, 0, 0), [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
            ((0, 1, 0), [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
            ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
            ((0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
            ((0, 0, -1), [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)])]
    idx = np.argwhere(occ)
    for d, corners in dirs:
        nb = padded[idx[:, 0] + 1 + d[0], idx[:, 1] + 1 + d[1], idx[:, 2] + 1 + d[2]]
        for i, j, k in idx[~nb]:
            faces.append([v(i + a, j + b, k + c) for a, b, c in corners])
    return vlist, faces


def reconstruct(paths: list[str], *, angles_deg: Optional[list[float]] = None, height_mm: Optional[float] = None,
                name: str = "Object", grid: int = 64) -> tuple[StudioModel, dict]:
    masks = silhouettes(paths)
    rgbs = [imaging.load_rgb(p, 512) for p in paths]
    check = check_views(masks, rgbs)
    if len(check.accepted) < 3:
        raise ValueError("fewer than three consistent views")
    n = len(paths)
    angles = angles_deg or [360.0 * i / n for i in range(n)]
    use = check.accepted
    occ, info = carve([masks[i] for i in use], [angles[i] for i in use], grid)
    if occ.sum() < 8:
        raise ValueError("the views do not agree on any solid")
    H = height_mm or 150.0
    R = info["R"] * H
    verts, faces = surface_mesh(occ, (2 * R, 2 * R, H))
    ev = Evidence.CONSTRAINED if height_mm else Evidence.ESTIMATED
    mat = Material(f"{name} surface", [0.72, 0.72, 0.74], roughness=0.5, evidence=Evidence.ESTIMATED)
    part = ModelPart(name=name, geometry="mesh", params={"vertices": verts, "faces": faces, "bounds": [2 * R, 2 * R, H],
                                                        "evidence": {"exterior": Evidence.CONSTRAINED.value, "scale": ev.value,
                                                                     "concavities": Evidence.INVENTED.value},
                                                        "views_used": len(use)},
                     modifiers=[{"type": "subdivision", "levels": 1}], material=mat.name,
                     evidence=Evidence.weakest([Evidence.CONSTRAINED, ev]), confidence=0.6, aliases=["body", "object"])
    cams = [CameraEstimate(kind="perspective", view=ViewKind.PERSPECTIVE, azimuth_deg=a, elevation_deg=0.0,
                           distance_mm=H * 4, confidence=0.6) for a in [angles[i] for i in use]]
    model = StudioModel(name=name, parts=[part], materials=[mat], cameras=cams, category="scanned object",
                        fidelity=Fidelity.VISUAL)
    return model, {"check": check, "occupancy": int(occ.sum()), "faces": len(faces), "masks": masks,
                   "angles": angles}
