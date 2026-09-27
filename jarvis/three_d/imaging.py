"""Small, deterministic image measurements shared by the classifier and the engines.

Everything works on numpy arrays and runs locally. No model, no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


def load_rgb(path: str, max_side: int = 1600) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        return np.asarray(im, dtype=np.uint8).copy()


def background_color(rgb: np.ndarray) -> np.ndarray:
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]).astype(np.float32)
    return np.median(border, axis=0)


def foreground_mask(rgb: np.ndarray, threshold: Optional[float] = None, bg: Optional[np.ndarray] = None) -> np.ndarray:
    """Pixels that differ from the backdrop. Holes inside the object stay holes.

    The threshold adapts: half of Otsu's split of the difference image, kept between 16 and 48,
    so a shaded side that is nearly the backdrop's colour is not cut off, and noise is not kept.
    """
    import cv2

    bg = background_color(rgb) if bg is None else bg
    diff = np.abs(rgb.astype(np.float32) - bg).sum(axis=2)
    if threshold is None:
        d8 = np.clip(diff / 3, 0, 255).astype(np.uint8)
        otsu, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        threshold = float(np.clip(otsu * 3 / 2, 16.0, 48.0))
    mask = (diff > threshold).astype(np.uint8)
    # Drop specks (compression noise) without moving any edge — a morphological opening with an
    # even kernel shifts the mask by a pixel, which is a pixel of contour error.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    small = np.nonzero(stats[1:, cv2.CC_STAT_AREA] < 4)[0] + 1
    if len(small):
        mask[np.isin(labels, small)] = 0
    return mask.astype(bool)


def filled_silhouette(mask: np.ndarray) -> np.ndarray:
    """The outer silhouette: the mask with every enclosed hole filled."""
    import cv2

    m = mask.astype(np.uint8)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros_like(m)
    cv2.drawContours(out, contours, -1, 1, thickness=cv2.FILLED)
    return out.astype(bool)


def bbox(mask: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


@dataclass
class Palette:
    colors: np.ndarray        # k x 3, uint8
    labels: np.ndarray        # h x w int, -1 for background
    shares: np.ndarray        # fraction of foreground per colour
    residual: float           # mean distance of fg pixels to their palette colour


def quantize(rgb: np.ndarray, mask: np.ndarray, k: int = 6) -> Palette:
    """k-means on foreground colours (OpenCV), merged when two centres are near-identical."""
    import cv2

    px = rgb[mask].astype(np.float32)
    if len(px) == 0:
        return Palette(np.zeros((0, 3), np.uint8), np.full(mask.shape, -1), np.zeros(0), 0.0)
    sample = px if len(px) <= 60000 else px[np.random.default_rng(0).choice(len(px), 60000, replace=False)]
    k = max(1, min(k, len(np.unique(sample.astype(np.uint8), axis=0))))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, _, centres = cv2.kmeans(sample, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    # merge near-duplicate centres
    keep: list[np.ndarray] = []
    for c in centres:
        if all(np.linalg.norm(c - q) > 30 for q in keep):
            keep.append(c)
    centres = np.array(keep, np.float32)
    d = np.linalg.norm(px[:, None, :] - centres[None, :, :], axis=2)
    lab = d.argmin(axis=1)
    residual = float(d[np.arange(len(px)), lab].mean())
    labels = np.full(mask.shape, -1, np.int32)
    labels[mask] = lab
    shares = np.bincount(lab, minlength=len(centres)) / len(lab)
    order = np.argsort(-shares)
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order))
    labels[mask] = remap[lab]
    return Palette(centres[order].round().astype(np.uint8), labels, shares[order], residual)


def mirror_symmetry(mask: np.ndarray) -> float:
    """IoU of the silhouette with its left-right mirror about its own centre column."""
    b = bbox(mask)
    if b is None:
        return 0.0
    x0, y0, x1, y1 = b
    m = mask[y0:y1, x0:x1]
    f = m[:, ::-1]
    inter = np.logical_and(m, f).sum()
    union = np.logical_or(m, f).sum()
    return float(inter / union) if union else 0.0


def stroke_width(mask: np.ndarray) -> float:
    """Typical stroke thickness in pixels (2 × median distance to the edge, over the skeleton-ish core)."""
    import cv2

    if not mask.any():
        return 0.0
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    core = dist[dist >= np.percentile(dist[mask], 75)]
    return float(2 * np.median(core)) if len(core) else 0.0


def shading(rgb: np.ndarray, mask: np.ndarray) -> float:
    """How much colour varies smoothly inside the object (0 flat fill … 1 photo-like shading)."""
    import cv2

    if mask.sum() < 50:
        return 0.0
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    inner = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    if inner.sum() < 30:
        return 0.0
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    g = np.hypot(gx, gy)[inner]
    # Flat fills: gradient ~0 almost everywhere. Shading: many small, non-zero gradients.
    gentle = np.logical_and(g > 2, g < 60).mean()
    return float(min(1.0, gentle * 2.5))


def iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 1.0


def contour_distance(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(mean, 95th-percentile) symmetric distance between the two silhouettes' edges, px."""
    import cv2

    def edge(m):
        m = m.astype(np.uint8)
        return (m - cv2.erode(m, np.ones((3, 3), np.uint8))).astype(bool)

    ea, eb = edge(a), edge(b)
    if not ea.any() or not eb.any():
        return float("inf"), float("inf")
    da = cv2.distanceTransform((~eb).astype(np.uint8), cv2.DIST_L2, 3)[ea]
    db = cv2.distanceTransform((~ea).astype(np.uint8), cv2.DIST_L2, 3)[eb]
    both = np.concatenate([da, db])
    return float(both.mean()), float(np.percentile(both, 95))


def delta_e(rgb1, rgb2) -> float:
    """CIE76 colour difference between two sRGB colours (0..255)."""
    import cv2

    lab = cv2.cvtColor(np.array([[rgb1, rgb2]], np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[..., 0] *= 100 / 255
    lab[..., 1:] -= 128
    return float(np.linalg.norm(lab[0, 0] - lab[0, 1]))
