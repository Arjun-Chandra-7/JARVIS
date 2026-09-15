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
DEFAULT_DETAIL = 0.8

# The size the picture is worked at, measured by its shorter side. This is a quality setting, not
# a performance one, and both directions are worse: at 1000 the lines are clean and confident but
# the drawing is bare, and at 1800 the extra detail arrives as noise that reads as speckle. 1400
# is where the face keeps its features without the shading breaking up.
WORKING_SIZE = 1000

# How the tone is laid down. More levels and tighter spacing is darker, not more detailed: the
# first attempt used a fixed mid-grey cutoff and covered the whole canvas at every level.
HATCH_LEVELS = 5
HATCH_SPACING = 10
HATCH_COVERAGE = 0.55

# How hard local contrast is pushed before the tone is read. Higher brings out detail inside a
# bright face; too high and flat areas fill with the texture of their own noise.
CLAHE_CLIP = 2.5
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


_NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _trace(edges) -> list:
    """Every line in the edge map, followed end to end, the way a pen would.

    findContours traces *around* a line and back, so a one-pixel stroke comes out as a
    there-and-back loop of twice the length, which simplification then turns into a zigzag. That
    is where the speckle came from: not from the edges, which were already a clean drawing, but
    from outlining them instead of following them.

    Walking the ink gives each line once, in the order a hand would draw it. Lines are started at
    their ends where they have any, so a stroke runs tip to tip rather than out from its middle.
    """
    import numpy as np

    todo = {(int(y), int(x)) for y, x in zip(*np.nonzero(edges > 0))}

    def around(point):
        y, x = point
        for dy, dx in _NEIGHBOURS:
            neighbour = (y + dy, x + dx)
            if neighbour in todo:
                yield neighbour

    ends = [p for p in todo if sum(1 for _ in around(p)) == 1]
    paths = []
    while ends or todo:
        if ends:
            start = ends.pop()
            if start not in todo:
                continue
        else:
            start = next(iter(todo))          # a closed loop has no ends to start from
        path = [start]
        todo.discard(start)
        while True:
            nxt = next(around(path[-1]), None)
            if nxt is None:
                break
            todo.discard(nxt)
            path.append(nxt)
        if len(path) >= MIN_STROKE_POINTS:
            paths.append([(x, y) for y, x in path])
    return paths


def _smooth(points, passes: int = 2):
    """Round a pixel-stepped path into something a hand could have drawn.

    Contour points step between neighbouring pixels, so every line arrives as a staircase. Two
    passes of Chaikin's corner cutting turn that into a curve without moving it anywhere it was
    not already.
    """
    import numpy as np

    path = np.asarray(points, dtype=float)
    for _ in range(passes):
        if len(path) < 3:
            break
        a, b = path[:-1], path[1:]
        cut = np.empty((2 * len(a), 2))
        cut[0::2] = 0.75 * a + 0.25 * b
        cut[1::2] = 0.25 * a + 0.75 * b
        path = np.vstack([path[0], cut, path[-1]])
    return path


def _hatch(tone, levels: int, spacing: int, coverage: float, min_run: int = 6) -> list:
    """Darkness as stroke density, the way an engraving works.

    This is the part that makes a portrait a portrait. Edge detection finds boundaries — where
    the jaw is — and has no idea that the cheek is lighter than the shadow beside it, so a
    drawing made only of edges does not read as a face however many lines it has. An engraver
    does the opposite: they lay down more lines where it is darker.

    Each pass covers everything below a brightness threshold at its own angle, so the darkest
    areas receive every pass and the lightest none. Thresholds come from the picture's own
    histogram rather than the 0-255 scale: a dark painting has nearly every pixel below a fixed
    mid-grey, and evenly spaced cutoffs cover the whole canvas at every level — the first attempt
    came out solid black.
    """
    import numpy as np

    angles = (20, 70, -25, 115, 45, -60)
    h, w = tone.shape
    strokes = []
    cuts = [float(np.percentile(tone, q)) for q in np.linspace(coverage * 100, 4, levels)]
    span = int(np.hypot(h, w))
    for i, cutoff in enumerate(cuts):
        mask = tone < cutoff
        if not mask.any():
            continue
        angle = np.deg2rad(angles[i % len(angles)])
        dx, dy = float(np.cos(angle)), float(np.sin(angle))
        px, py = -dy, dx            # step perpendicular to lay parallel lines across the picture
        for offset in range(-span, span, spacing):
            x0 = w / 2 + px * offset - dx * span
            y0 = h / 2 + py * offset - dy * span
            run_start = None
            for t in range(2 * span):
                x = int(x0 + dx * t)
                y = int(y0 + dy * t)
                inside = 0 <= x < w and 0 <= y < h and mask[y, x]
                if inside and run_start is None:
                    run_start = (x, y)
                elif not inside and run_start is not None:
                    end = (int(x0 + dx * (t - 1)), int(y0 + dy * (t - 1)))
                    if abs(end[0] - run_start[0]) + abs(end[1] - run_start[1]) >= min_run:
                        strokes.append([run_start, end])
                    run_start = None
    return strokes


def from_image(path: str | Path, budget: int = DEFAULT_BUDGET,
               detail: float = DEFAULT_DETAIL,
               short_side: int = WORKING_SIZE) -> Optional[Plan]:
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

    # Tone first, features second. Hatching is what makes it recognisable; the traced edges put
    # the eyes, mouth and fingers back on top of it.
    # Local contrast before anything else. Hatching thresholds are taken across the whole picture,
    # so a face brighter than the rest of the canvas sits above every one of them and receives no
    # strokes at all — Vermeer's girl, the figure in The Scream and Einstein all came out as white
    # cutouts with only an outline, while the Mona Lisa worked because her face happens to be
    # mid-tone. Equalising in tiles gives the inside of a bright region its own range, so the
    # shadow beside a nose is dark relative to the cheek even when the whole face is light.
    equalised = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=(8, 8)).apply(image)
    tone = cv2.GaussianBlur(equalised, (0, 0), 2.0)
    hatch_strokes = _hatch(tone, levels=HATCH_LEVELS, spacing=HATCH_SPACING,
                           coverage=HATCH_COVERAGE)

    found = _trace(_edges(image, detail))
    if not found and not hatch_strokes:
        return None

    # Rank by how far a contour travels across the picture, not by how many points it has.
    # Ranking by point count put noise first: a jittery twenty-pixel fragment survives
    # simplification with more points than a long smooth curve, so a painting came out as a
    # scatter of unrecognisable ticks while the lines that describe it were never drawn.
    diagonal = float(np.hypot(h, w))
    # Low on purpose. A tight filter here threw away about eighty-five per cent of the traced
    # lines and left a picture that was mostly empty: the edge map's quality comes from all the
    # medium-length strokes — an eyelid, a knuckle, a fold — not from a handful of long ones.
    # What looked like speckle in earlier attempts turned out to be several drawings stacked on
    # a board that had never been cleared, not short strokes.
    min_points = max(MIN_STROKE_POINTS, int(14 - 8 * detail))

    measured = [(len(path), path) for path in found]
    measured = [(n, path) for n, path in measured if n >= min_points]
    measured.sort(key=lambda pair: pair[0], reverse=True)

    simplified = []
    for _n, path in measured:
        # Simplify lightly, then round the corners. A traced path steps between neighbouring
        # pixels, so it arrives as a staircase; simplifying alone leaves a polygon, and Chaikin
        # turns that into a curve without moving it off the line it came from.
        points = cv2.approxPolyDP(
            np.asarray(path, dtype=np.float32).reshape(-1, 1, 2),
            max(0.7, 1.6 - detail), False).reshape(-1, 2)
        if len(points) < MIN_STROKE_POINTS:
            continue
        simplified.append(_smooth(points))
    # Hatching is laid down first so the features drawn over it stay on top.
    simplified = [np.asarray(h, dtype=float) for h in hatch_strokes] + simplified
    if not simplified:
        return None

    strokes: list[list[tuple[int, int]]] = []
    spent = 0
    for points in simplified:
        if spent >= budget:
            break
        room = budget - spent
        take = points[:room] if len(points) > room else points
        # Two points is a valid stroke: a hatch line is straight, and requiring four discarded
        # every one of them — which is why the tone never appeared.
        if len(take) < 2:
            continue
        strokes.append([(int(px), int(py)) for px, py in take])
        spent += len(take)

    if not strokes:
        return None
    return Plan(strokes=strokes, points=spent, source_size=(w, h))
