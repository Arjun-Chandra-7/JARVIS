"""Finding a right-angled triangle in a video frame — or saying there isn't one clearly enough.

Used by the screen-aware Pythagoras lesson to trace the triangle the teacher drew instead of
drawing a separate one. Wrong here is worse than absent: a trace over the wrong lines teaches
the wrong picture. So a candidate has to pass all of:

  * three corners, convex, not tiny (at least 3 % of the searched region);
  * one angle within 12° of a right angle;
  * its sides actually drawn: edge pixels found along most of each side, not just a closed
    contour that happens to have three corners.

The confidence combines those. Below ``MIN_CONFIDENCE`` the caller draws a standalone triangle
and says so. The image is only ever held in memory; nothing is written.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

MIN_CONFIDENCE = 0.6


@dataclass
class Triangle:
    right: tuple[float, float]
    a: tuple[float, float]            # far end of one leg
    b: tuple[float, float]            # far end of the other
    confidence: float
    angle_error: float
    support: float                    # fraction of the sides with edge pixels under them
    area_fraction: float


def _angle(p, q, r) -> float:
    """Angle at q, in degrees."""
    v1 = (p[0] - q[0], p[1] - q[1])
    v2 = (r[0] - q[0], r[1] - q[1])
    n = math.hypot(*v1) * math.hypot(*v2) or 1.0
    c = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / n))
    return math.degrees(math.acos(c))


def _support(edges, p, q, radius: int = 3) -> float:
    """Fraction of points along p–q with an edge pixel within ``radius``."""
    import numpy as np

    h, w = edges.shape
    n = max(8, int(math.hypot(q[0] - p[0], q[1] - p[1]) / 4))
    hits = 0
    for i in range(n + 1):
        t = 0.08 + 0.84 * i / n                  # the corners are where lines meet anyway; skip them
        x = int(round(p[0] + (q[0] - p[0]) * t))
        y = int(round(p[1] + (q[1] - p[1]) * t))
        x0, x1 = max(0, x - radius), min(w, x + radius + 1)
        y0, y1 = max(0, y - radius), min(h, y + radius + 1)
        if x0 < x1 and y0 < y1 and np.any(edges[y0:y1, x0:x1]):
            hits += 1
    return hits / (n + 1)


def find(image, min_area_fraction: float = 0.03) -> Optional[Triangle]:
    """The best right-angled triangle in ``image`` (a BGR or grey numpy array), or None."""
    import cv2
    import numpy as np

    if image is None or image.size == 0:
        return None
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    h, w = grey.shape
    region_area = float(h * w)
    blur = cv2.GaussianBlur(grey, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 120)
    closed = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best: Optional[Triangle] = None
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area_fraction * region_area:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.035 * peri, True)
        if len(approx) != 3 or not cv2.isContourConvex(approx):
            continue
        pts = [tuple(map(float, p[0])) for p in approx]
        angles = [_angle(pts[(i - 1) % 3], pts[i], pts[(i + 1) % 3]) for i in range(3)]
        ri = min(range(3), key=lambda i: abs(angles[i] - 90))
        err = abs(angles[ri] - 90)
        if err > 12:
            continue
        right, a, b = pts[ri], pts[(ri + 1) % 3], pts[(ri + 2) % 3]
        sup = min(_support(edges, right, a), _support(edges, right, b), _support(edges, a, b))
        if sup < 0.5:
            continue
        frac = area / region_area
        # A contour traced round both sides of a thick line is found twice; either is fine.
        conf = (0.45 * sup + 0.35 * (1 - err / 12) + 0.2 * min(1.0, frac / 0.12))
        cand = Triangle(right, a, b, round(conf, 3), round(err, 2), round(sup, 3), round(frac, 4))
        if best is None or cand.confidence > best.confidence:
            best = cand
    return best


def to_screen(t: Triangle, region: dict, image_size: tuple[int, int]) -> Triangle:
    """Map a triangle found in a cropped screenshot back to monitor logical coordinates.

    ``region`` is the crop in logical pixels; ``image_size`` the crop's size in image pixels
    (physical, when the screenshot was taken at a fractional scale)."""
    sx = region["w"] / image_size[0]
    sy = region["h"] / image_size[1]
    m = lambda p: (round(region["x"] + p[0] * sx, 1), round(region["y"] + p[1] * sy, 1))  # noqa: E731
    return Triangle(m(t.right), m(t.a), m(t.b), t.confidence, t.angle_error, t.support, t.area_fraction)
