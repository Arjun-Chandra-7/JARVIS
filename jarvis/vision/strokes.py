"""Turn a picture into strokes a pointer can draw.

A whiteboard is a line medium: it has no fills, no tones, no brush. So the question is not "what
colour is each pixel" but "where are the lines", which is edge detection followed by contour
tracing — a well-understood problem with no model in it anywhere.

The part that matters for this being usable rather than a demo is the budget. A photograph yields
tens of thousands of contour points, and every point is one dispatched mouse event over a
websocket; drawing all of them would take several minutes and look like a hang. So contours are
simplified with Douglas-Peucker, ranked by how much of the picture they actually describe, and cut
off at a point budget. Fewer, longer strokes read as a drawing; many short ones read as noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# One mouse event per point, so this is really a time budget. ~1200 points draws in a few seconds.
DEFAULT_BUDGET = 1200
MIN_STROKE_POINTS = 4


@dataclass
class Plan:
    strokes: list[list[tuple[int, int]]]
    points: int
    source_size: tuple[int, int]

    def scaled_into(self, x: int, y: int, w: int, h: int,
                    margin: float = 0.06) -> list[list[tuple[int, int]]]:
        """The same drawing, fitted inside a box on screen, aspect ratio kept."""
        sw, sh = self.source_size
        if not sw or not sh:
            return []
        pad_x, pad_y = w * margin, h * margin
        box_w, box_h = max(1.0, w - 2 * pad_x), max(1.0, h - 2 * pad_y)
        scale = min(box_w / sw, box_h / sh)
        # Centre whatever is left over, so a portrait does not sit against one edge.
        off_x = x + pad_x + (box_w - sw * scale) / 2
        off_y = y + pad_y + (box_h - sh * scale) / 2
        return [[(int(off_x + px * scale), int(off_y + py * scale)) for px, py in stroke]
                for stroke in self.strokes]


def _edges(gray, detail: float):
    import cv2

    # Blur first: without it, JPEG noise and skin texture become "edges" and the drawing is fur.
    # Bilateral smoothing before the Gaussian keeps real boundaries sharp while flattening the
    # tonal variation inside them, which is what separates a painting from a line drawing and was
    # leaving the result covered in short ticks.
    smoothed = cv2.bilateralFilter(gray, 9, 60, 60)
    blurred = cv2.GaussianBlur(smoothed, (5, 5), 0)
    # Thresholds from the image's own median, so one setting works for a photograph and a diagram.
    import numpy as np

    median = float(np.median(blurred))
    spread = max(0.05, min(0.9, 1.0 - detail))
    low = int(max(0, (1.0 - spread) * median))
    high = int(min(255, (1.0 + spread) * median))
    return cv2.Canny(blurred, low, max(low + 1, high))


def from_image(path: str | Path, budget: int = DEFAULT_BUDGET,
               detail: float = 0.5, max_side: int = 900) -> Optional[Plan]:
    """A drawable plan for the picture at `path`, or None when it yields nothing worth drawing."""
    import cv2
    import numpy as np

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    h, w = image.shape[:2]
    if max(h, w) > max_side:                  # detail beyond this cannot survive the point budget
        scale = max_side / float(max(h, w))
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = image.shape[:2]

    found, _ = cv2.findContours(_edges(image, detail), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    if not found:
        return None

    # Rank by how far a contour travels across the picture, not by how many points it has.
    # Ranking by point count put noise first: a jittery twenty-pixel fragment survives
    # simplification with more points than a long smooth curve, so a painting came out as a
    # scatter of unrecognisable ticks while the lines that describe it were never drawn.
    diagonal = float(np.hypot(h, w))
    # Below this a contour describes texture rather than shape, and spends budget saying nothing.
    min_length = diagonal * 0.055
    measured = [(cv2.arcLength(c, False), c) for c in found]
    measured = [(length, c) for length, c in measured if length >= min_length]
    if not measured:
        return None
    measured.sort(key=lambda pair: pair[0], reverse=True)

    simplified = []
    for length, contour in measured:
        # Simplify in proportion to the contour's own size: the same absolute tolerance either
        # destroys a small shape or leaves a large one needlessly dense.
        epsilon = max(1.0, 0.004 * length)
        points = cv2.approxPolyDP(contour, epsilon, False).reshape(-1, 2)
        if len(points) >= MIN_STROKE_POINTS:
            simplified.append(points)
    if not simplified:
        return None

    strokes: list[list[tuple[int, int]]] = []
    spent = 0
    for points in simplified:
        if spent >= budget:
            break
        room = budget - spent
        take = points[:room] if len(points) > room else points
        if len(take) < MIN_STROKE_POINTS:
            continue
        strokes.append([(int(px), int(py)) for px, py in take])
        spent += len(take)

    if not strokes:
        return None
    return Plan(strokes=strokes, points=spent, source_size=(w, h))
