"""Scale, units and cameras: turning pixels into millimetres, honestly.

* ``parse_length`` / ``to_mm`` — "42 centimetres", "18 inches", "120" (unit unknown → ``None``
  unit: the caller must ask, never assume).
* ``dimension_labels`` — numbers read off a drawing (OCR, local only), kept as numbers and units.
  The text itself is never returned beyond the parsed value, and never logged.
* ``scale_from_dimensions`` — mm per pixel from a labelled overall dimension.
* ``estimate_camera`` — for one perspective picture: elevation from the ellipse a round rim makes
  (minor/major = sin(elevation)), otherwise a labelled guess.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .types import CameraEstimate, DetectedDimension, Evidence, ViewKind

UNIT_MM = {
    "mm": 1.0, "millimetre": 1.0, "millimeter": 1.0, "millimetres": 1.0, "millimeters": 1.0,
    "cm": 10.0, "centimetre": 10.0, "centimeter": 10.0, "centimetres": 10.0, "centimeters": 10.0,
    "m": 1000.0, "metre": 1000.0, "meter": 1000.0, "metres": 1000.0, "meters": 1000.0,
    "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4, "″": 25.4,
    "ft": 304.8, "foot": 304.8, "feet": 304.8, "'": 304.8,
}
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "half": 0.5}

_LEN = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?|\b(?:" + "|".join(_WORD_NUM) + r")\b)\s*"
    r"(?P<unit>millimet(?:re|er)s?|centimet(?:re|er)s?|met(?:re|er)s?|inch(?:es)?|feet|foot|mm|cm|in\b|ft\b|m\b|\"|″|')?",
    re.I)


def to_mm(value: float, unit: str) -> float:
    return float(value) * UNIT_MM[unit.lower()]


def parse_length(text: str) -> Optional[tuple[float, Optional[str]]]:
    """First length in ``text`` as (millimetres or raw number, unit). Unit None = ambiguous."""
    for m in _LEN.finditer(text or ""):
        raw = m.group("num").lower().replace(",", ".")
        num = float(_WORD_NUM.get(raw, raw)) if raw in _WORD_NUM else float(raw)
        unit = (m.group("unit") or "").lower()
        if unit:
            return to_mm(num, unit), unit
        return num, None
    return None


_AXIS_WORDS = {
    "width": ("wide", "width", "across", "long", "length"),
    "height": ("tall", "high", "height"),
    "depth": ("deep", "depth", "thick", "thickness"),
    "diameter": ("diameter", "across the top", "round"),
}


def parse_known_dimension(text: str) -> Optional[tuple[str, float, Optional[str]]]:
    """'The full object is 42 centimetres wide' → ('width', 420.0, 'centimetres')."""
    t = (text or "").lower()
    got = parse_length(t)
    if not got:
        return None
    value, unit = got
    for axis, words in _AXIS_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
            return axis, value, unit
    return "size", value, unit


# ----------------------------------------------------------------------------- drawings
_LABEL = re.compile(r"^\s*[ØøR⌀]?\s*(\d{1,5}(?:[.,]\d{1,3})?)\s*(mm|cm|in|m|\"|″)?\s*$", re.I)
_UNIT_NOTE = re.compile(r"(?i)\b(?:all\s+)?dimensions?\s+in\s+(mm|millimet\w+|cm|centimet\w+|inch(?:es)?|in)\b")


@dataclass
class Label:
    value: float
    unit: Optional[str]
    cx: float
    cy: float
    w: float
    h: float
    diameter: bool = False


def dimension_labels(words: Iterable) -> tuple[list[Label], Optional[str]]:
    """Parse OCR words into numeric labels and a drawing-wide unit note.

    ``words`` are objects with ``text``, ``left/top/right/bottom``. Anything that is not a
    number-with-optional-unit is dropped here — that is the whole of what text in an image can do.
    """
    labels: list[Label] = []
    note: Optional[str] = None
    joined = " ".join(getattr(w, "text", "") for w in words)
    m = _UNIT_NOTE.search(joined)
    if m:
        u = m.group(1).lower()
        note = "mm" if u.startswith("mil") or u == "mm" else "cm" if u.startswith("c") else "in"
    for w in words:
        text = str(getattr(w, "text", ""))
        mm = _LABEL.match(text.replace(" ", ""))
        if not mm:
            continue
        unit = (mm.group(2) or "").lower() or None
        if unit in ('"', "″"):
            unit = "in"
        labels.append(Label(float(mm.group(1).replace(",", ".")), unit,
                            (w.left + w.right) / 2, (w.top + w.bottom) / 2, w.right - w.left, w.bottom - w.top,
                            diameter=text.strip()[:1] in "Øø⌀"))
    return labels, note


def resolve_units(labels: list[Label], note: Optional[str], stated: Optional[str] = None) -> Optional[str]:
    """One unit for the drawing, or None when it is genuinely ambiguous (then: ask)."""
    units = {l.unit for l in labels if l.unit}
    if len(units) == 1:
        return units.pop()
    if len(units) > 1:
        return None
    return note or stated


def pair_dimensions(labels: list[Label], bbox: tuple[int, int, int, int], unit: str) -> list[DetectedDimension]:
    """Labels below/above the object measure horizontal spans; labels beside it, vertical ones.

    ``bbox`` is the object's silhouette box (x0, y0, x1, y1) in pixels. The overall span a label
    sits beside is the silhouette's extent on that axis; the pairing is by position, which is how
    a person reads a simple drawing too.
    """
    x0, y0, x1, y1 = bbox
    out = []
    for l in labels:
        inside_x = x0 - 5 <= l.cx <= x1 + 5
        inside_y = y0 - 5 <= l.cy <= y1 + 5
        if inside_x and (l.cy > y1 or l.cy < y0):
            axis, span = "horizontal", float(x1 - x0)
        elif inside_y and (l.cx > x1 or l.cx < x0):
            axis, span = "vertical", float(y1 - y0)
        else:
            continue
        out.append(DetectedDimension(value_mm=to_mm(l.value, l.unit or unit), raw_unit=l.unit or unit,
                                     axis=axis, span_px=span, confidence=0.9))
    return out


def scale_from_dimensions(dims: list[DetectedDimension]) -> tuple[float, float]:
    """(mm per pixel, agreement 0..1). The largest labelled span on each axis is the overall size."""
    best = {}
    for d in dims:
        if d.span_px > 0 and (d.axis not in best or d.value_mm > best[d.axis].value_mm):
            best[d.axis] = d
    if not best:
        return 0.0, 0.0
    scales = [d.value_mm / d.span_px for d in best.values()]
    s = sum(scales) / len(scales)
    agree = 1.0 - (max(scales) - min(scales)) / s if len(scales) > 1 else 0.8
    return s, max(0.0, min(1.0, agree))


# ----------------------------------------------------------------------------- cameras
def orthographic(view: ViewKind, frame_mm: float) -> CameraEstimate:
    return CameraEstimate(kind="orthographic", view=view, ortho_scale_mm=frame_mm, confidence=0.95,
                          evidence=Evidence.VERIFIED, azimuth_deg={"front": 0, "side": 90, "back": 180,
                                                                   "top": 0}.get(view.value, 0),
                          elevation_deg=90.0 if view == ViewKind.TOP else 0.0)


def elevation_from_ellipse(major_px: float, minor_px: float) -> float:
    """A circle seen from elevation e projects to an ellipse with minor/major = sin(e)."""
    if major_px <= 0:
        return 0.0
    ratio = max(0.0, min(1.0, minor_px / major_px))
    return math.degrees(math.asin(ratio))


def estimate_camera(mask, *, round_top: bool = False) -> CameraEstimate:
    """A first guess for one perspective picture: level, 50 mm, low confidence.

    The elevation is then *fitted* by ``fit_elevation`` — rendering the model from candidate
    cameras and keeping the one whose silhouette matches — rather than read off the image by a
    heuristic (a rim-ellipse rule misread a widening body as a steep view).
    """
    return CameraEstimate(kind="perspective", view=ViewKind.PERSPECTIVE, focal_mm=50.0,
                          confidence=0.3, evidence=Evidence.ESTIMATED)


ELEVATIONS = (0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 25.0, 30.0, 40.0)


def fit_elevation(score, candidates=ELEVATIONS) -> tuple[float, float, list]:
    """Analysis by synthesis: ``score(elevation) → IoU``; returns (best, its IoU, all scores).

    A coarse search, then one refinement step either side of the best — cheap (each score is a
    CPU rasterisation) and honest about how flat the optimum is.
    """
    scores = [(e, score(e)) for e in candidates]
    best, s = max(scores, key=lambda t: t[1])
    for e in (best - 2.0, best + 2.0):
        if 0.0 <= e <= 60.0:
            v = score(e)
            scores.append((e, v))
            if v > s:
                best, s = e, v
    return best, s, sorted(scores)
