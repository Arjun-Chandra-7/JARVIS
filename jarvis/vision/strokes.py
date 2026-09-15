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
# Drawing used to cost one websocket round trip per point, which capped this at about a
# thousand. Pipelining the mouse events took a 500-point drawing from 9.8 s to 0.1 s, so the
# budget is now set by what looks good rather than by what finishes in time.
# Density is bounded by the pen, not by this. Measured on a three-pixel line: 1,900 points reads
# as a drawing, 14,000 fills the dark areas into a solid blob because neighbouring contours are
# closer together than the stroke is wide. So the budget is generous and the detail dial is what
# actually decides, and it is set where a normal pen still shows the lines apart.
# Every point is one entry in a list handed to the page, not a round trip, so the ceiling here is
# what a pen can show rather than what finishes in time. With the tool thinned to its finest
# setting first — see browser.thin_pen — seventeen thousand points draws in three seconds and
# comes out as hair, shaded features and individual fingers. At the default four-pixel width the
# same drawing is a blob, which is why thinning is not optional.
DEFAULT_BUDGET = 40000
DEFAULT_DETAIL = 0.9
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
    """Edges, as many or as few as `detail` asks for.

    Smoothing is the dial that matters. Bilateral smoothing keeps real boundaries sharp while
    flattening tonal variation inside them — the difference between a painting and a line
    drawing — but turned up hard it also erases the lines that make a face a face. Measured on
    the Mona Lisa at 900px: a strong filter yields 92 usable contours, a light one 629, none at
    all 3157 (which is noise). So the strength moves with `detail` instead of being fixed.
    """
    import cv2
    import numpy as np

    detail = max(0.0, min(1.0, detail))
    # Smoothing stays strong whatever the detail setting. Turning it down does produce more
    # edges — 92 contours becomes 629 — but they are the same lines broken into pieces, and the
    # drawing comes out as a field of ticks rather than a face. Detail is bought further down,
    # by admitting shorter contours and following each one more faithfully, which adds line
    # without fragmenting what is already there.
    smoothed = cv2.bilateralFilter(gray, 9, 60, 60)
    blurred = cv2.GaussianBlur(smoothed, (5, 5), 0)

    # Thresholds from the image's own median, so one setting works for a photograph and a diagram.
    median = float(np.median(blurred))
    spread = 0.40 + 0.20 * detail
    low = int(max(0, (1.0 - spread) * median))
    high = int(min(255, (1.0 + spread) * median))
    return cv2.Canny(blurred, low, max(low + 1, high))


def from_image(path: str | Path, budget: int = DEFAULT_BUDGET,
               detail: float = DEFAULT_DETAIL, short_side: int = 1800) -> Optional[Plan]:
    """A drawable plan for the picture at `path`, or None when it yields nothing worth drawing."""
    import cv2
    import numpy as np

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    h, w = image.shape[:2]
    # Worked at a fixed size in both directions. Shrinking a large photograph keeps the budget
    # honest; enlarging a small one samples its contours more finely, which is worth real points —
    # the same painting at 800px gives about five thousand and at 1800px about seventeen thousand.
    # Sized by the shorter side. Using the longer one made a portrait far coarser than a
    # landscape of the same "size" — the Mona Lisa came out 1207 wide where a test that fixed the
    # width to 1800 found nearly four times as many contour points.
    if min(h, w) != short_side:
        scale = short_side / float(min(h, w))
        image = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))),
                           interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        h, w = image.shape[:2]

    found, _ = cv2.findContours(_edges(image, detail), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    if not found:
        return None

    # Rank by how far a contour travels across the picture, not by how many points it has.
    # Ranking by point count put noise first: a jittery twenty-pixel fragment survives
    # simplification with more points than a long smooth curve, so a painting came out as a
    # scatter of unrecognisable ticks while the lines that describe it were never drawn.
    diagonal = float(np.hypot(h, w))
    # Below this a contour describes texture rather than shape. How short is "too short" is the
    # other half of the detail dial: at 0.5 it is a twentieth of the picture, at 1.0 a two
    # hundredth, which is the difference between an outline and a drawing with a face in it.
    min_length = diagonal * (0.055 - 0.0567 * detail)
    measured = [(cv2.arcLength(c, False), c) for c in found]
    measured = [(length, c) for length, c in measured if length >= min_length]
    if not measured:
        return None
    measured.sort(key=lambda pair: pair[0], reverse=True)

    simplified = []
    for length, contour in measured:
        # Simplify in proportion to the contour's own size: the same absolute tolerance either
        # destroys a small shape or leaves a large one needlessly dense.
        # How faithfully each contour is followed. Straightening a curve is what made the first
        # attempts look like a rubbing rather than a drawing.
        # Near-zero at full detail: following the contour as it is, rather than straightening it.
        epsilon = max(0.5, (0.004 - 0.0039 * detail) * length)
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
