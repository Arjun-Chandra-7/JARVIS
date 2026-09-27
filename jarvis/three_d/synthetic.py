"""Generated, fictional references with known ground truth — for tests and demonstrations.

Nothing here is a real logo, product or drawing. Everything is drawn from numbers, so the true
contour, dimensions and shape are known exactly and can be compared against.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

FONT_CANDIDATES = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                   "/usr/share/fonts/TTF/DejaVuSans.ttf")


def _font(size: int):
    from PIL import ImageFont

    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


# ----------------------------------------------------------------------------- A: a logo
LOGO_RED = (200, 30, 45)
LOGO_BLUE = (25, 60, 170)


def logo(path: str, scale: int = 2, injection: Optional[str] = None) -> dict:
    """A two-colour badge: a red ring with a blue arrow through a gap. Returns the truth."""
    from PIL import Image, ImageDraw

    W, H = 300 * scale, 200 * scale
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    cx, cy = 110 * scale, 100 * scale
    R, r = 75 * scale, 45 * scale
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=LOGO_RED)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255))
    arrow = [(95, 88), (215, 88), (215, 70), (260, 100), (215, 130), (215, 112), (95, 112)]
    d.polygon([(x * scale, y * scale) for x, y in arrow], fill=LOGO_BLUE)
    if injection:
        d.text((10 * scale, 180 * scale), injection, fill=(90, 90, 90), font=_font(7 * scale))
    im.save(path)
    truth = np.asarray(im.convert("RGB")).astype(int)
    mask = np.abs(truth - 255).sum(axis=2) > 48
    return {"mask": mask, "colors": 2, "ring": (cx, cy, R, r), "size_px": (W, H)}


# ----------------------------------------------------------------------------- B: a drawing
@dataclass
class Box:
    name: str
    x: tuple          # (min, max) mm
    y: tuple
    z: tuple


@dataclass
class Cyl:
    name: str
    cx: float
    cy: float
    r: float
    z: tuple


@dataclass
class Bracket:
    """A fictional machined bracket: base plate, a round post, two identical corner blocks."""
    parts: list = field(default_factory=lambda: [
        Box("base", (0, 120), (0, 80), (0, 10)),
        Cyl("post", 60, 50, 15, (10, 70)),
        Box("block_l", (5, 25), (5, 25), (10, 25)),
        Box("block_r", (95, 115), (5, 25), (10, 25)),
    ])

    @property
    def size(self):
        return 120.0, 80.0, 70.0


def _rect(d, x0, y0, x1, y1, w):
    d.rectangle([x0, y0, x1, y1], outline=(0, 0, 0), width=w)


def _dim_h(d, x0, x1, y, label, font, w):
    d.line([x0, y, x1, y], fill=(0, 0, 0), width=1)
    for x in (x0, x1):
        d.line([x, y - 6, x, y + 6], fill=(0, 0, 0), width=1)
    tw = d.textlength(label, font=font)
    d.text(((x0 + x1) / 2 - tw / 2, y + 6), label, fill=(0, 0, 0), font=font)


def _dim_v(d, y0, y1, x, label, font, w):
    d.line([x, y0, x, y1], fill=(0, 0, 0), width=1)
    for y in (y0, y1):
        d.line([x - 6, y, x + 6, y], fill=(0, 0, 0), width=1)
    d.text((x + 10, (y0 + y1) / 2 - font.size / 2), label, fill=(0, 0, 0), font=font)


def drawing(view: str, path: str, obj: Optional[Bracket] = None, px_per_mm: float = 4.0, unit_note: bool = True,
            labels: bool = True, unit_suffix: str = "") -> dict:
    """One orthographic view, black outlines on white, with overall dimensions labelled."""
    from PIL import Image, ImageDraw

    obj = obj or Bracket()
    sx, sy, sz = obj.size
    horiz, vert = {"front": (sx, sz), "side": (sy, sz), "top": (sx, sy)}[view]
    margin = 70
    W, H = int(horiz * px_per_mm + 2 * margin + 60), int(vert * px_per_mm + 2 * margin + 20)
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    lw = 2
    ox, oy = margin, margin + vert * px_per_mm       # origin: bottom-left of the object

    def P(u, v):
        return ox + u * px_per_mm, oy - v * px_per_mm

    for part in obj.parts:
        if isinstance(part, Box):
            u, v = {"front": (part.x, part.z), "side": (part.y, part.z), "top": (part.x, part.y)}[view]
            (x0, y1), (x1, y0) = P(u[0], v[0]), P(u[1], v[1])
            _rect(d, x0, y0, x1, y1, lw)
        else:
            if view == "top":
                (x0, y1), (x1, y0) = P(part.cx - part.r, part.cy - part.r), P(part.cx + part.r, part.cy + part.r)
                d.ellipse([x0, y0, x1, y1], outline=(0, 0, 0), width=lw)
            else:
                c = part.cx if view == "front" else part.cy
                (x0, y1), (x1, y0) = P(c - part.r, part.z[0]), P(c + part.r, part.z[1])
                _rect(d, x0, y0, x1, y1, lw)
    font = _font(16)
    if labels:
        _dim_h(d, *[P(0, 0)[0], P(horiz, 0)[0]], oy + 22, f"{horiz:g}{unit_suffix}", font, lw)
        _dim_v(d, P(0, vert)[1], P(0, 0)[1], P(horiz, 0)[0] + 22, f"{vert:g}{unit_suffix}", font, lw)
    if unit_note:
        d.text((8, 8), "ALL DIMENSIONS IN MM", fill=(0, 0, 0), font=_font(13))
    im.save(path)
    return {"px_per_mm": px_per_mm, "view": view, "size_mm": (horiz, vert)}


# ----------------------------------------------------------------------------- C: a product
def vase_profile(height: float = 180.0) -> list:
    """(radius, z) of a fictional vase, mm — the hidden ground truth for the single-view slice."""
    pts = []
    for i in range(41):
        t = i / 40
        z = t * height
        r = 38 + 22 * math.sin(math.pi * (t * 0.9 + 0.05)) - 16 * t ** 3
        pts.append((round(r, 3), round(z, 3)))
    return [(0.0, 0.0)] + pts + [(0.0, height)]


def cube_turntable_truth() -> dict:
    return {"size": (60.0, 40.0, 50.0)}
