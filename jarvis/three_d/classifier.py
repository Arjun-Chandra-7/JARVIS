"""Which kind of reconstruction a set of references calls for — measured, not guessed.

Features come from ``imaging`` (colours, flatness, strokes, shading, symmetry), how many views
there are and what the user called them, and whether the drawing carries numbers. The words of
the request decide only intent (artistic expansion, exact dimensions, printing); they never
override what the pictures show is possible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import imaging
from .types import Mode, ReferenceAsset, ViewKind


@dataclass
class Features:
    colors: int
    flatness: float
    shading: float
    line_art: bool
    fill_ratio: float
    stroke_px: float
    symmetry: float
    aspect: float
    has_numbers: bool = False

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class Classification:
    mode: Mode
    confidence: float
    reasons: list = field(default_factory=list)
    features: list = field(default_factory=list)
    exact_requested: bool = False
    purpose: str = ""


_ARTISTIC = re.compile(r"(?i)\b(?:invent|imagine|complete\s+3d\s+character|full\s+(?:3d\s+)?(?:model|character)"
                       r"|unseen|make\s+up|stylis|in\s+the\s+same\s+style)\b")
_EXACT = re.compile(r"(?i)\b(?:exact(?:ly)?|precise(?:ly)?|to\s+scale|accurate\s+dimensions?|real\s+dimensions?)\b")
_PRINT = re.compile(r"(?i)\b(?:3d[\s-]?print\w*|printable|stl)\b")
_GAME = re.compile(r"(?i)\b(?:game[\s-]?ready|for\s+a\s+game|unity|unreal|godot|low[\s-]?poly)\b")


def features(rgb: np.ndarray, has_numbers: bool = False) -> Features:
    mask = imaging.foreground_mask(rgb)
    sil = imaging.filled_silhouette(mask)
    b = imaging.bbox(sil) or (0, 0, 1, 1)
    bw, bh = b[2] - b[0], b[3] - b[1]
    fill = float(mask.sum() / max(1, sil.sum()))
    pal = imaging.quantize(rgb, mask, k=8)
    significant = int((pal.shares > 0.01).sum())
    flat = float(max(0.0, 1.0 - pal.residual / 40.0))
    stroke = imaging.stroke_width(mask)
    line_art = fill < 0.35 and stroke < max(4.0, 0.02 * max(bw, bh))
    return Features(colors=significant, flatness=flat, shading=imaging.shading(rgb, mask), line_art=line_art,
                    fill_ratio=fill, stroke_px=stroke, symmetry=imaging.mirror_symmetry(sil),
                    aspect=bw / max(1, bh), has_numbers=has_numbers)


def classify(refs: list[ReferenceAsset], request: str = "", *, feats: Optional[list[Features]] = None,
             numbers: Optional[list[bool]] = None) -> Classification:
    """Pick a mode for these references. ``feats`` may be passed in (tests, or already measured)."""
    if feats is None:
        numbers = numbers or [False] * len(refs)
        feats = [features(imaging.load_rgb(r.path, 1024), n) for r, n in zip(refs, numbers)]
    reasons: list[str] = []
    exact = bool(_EXACT.search(request or ""))
    purpose = "print" if _PRINT.search(request or "") else "game" if _GAME.search(request or "") else ""
    views = {r.view for r in refs}
    ortho_views = views & {ViewKind.FRONT, ViewKind.SIDE, ViewKind.TOP, ViewKind.BACK}
    all_line_art = bool(feats) and all(f.line_art for f in feats)
    any_numbers = any(f.has_numbers for f in feats)

    if _ARTISTIC.search(request or ""):
        reasons.append("the request asks to invent what the reference doesn't show")
        return Classification(Mode.ARTISTIC, 0.8, reasons, feats, exact, purpose)
    frames = sum(1 for r in refs if r.source == "video_frame")
    if frames >= 4 or (len(refs) >= 4 and len(ortho_views) < 2):
        reasons.append(f"{len(refs)} views of the same object from different angles")
        return Classification(Mode.MULTIVIEW, 0.75, reasons, feats, exact, purpose)
    if (len(ortho_views) >= 2 or any_numbers) and all_line_art:
        reasons.append("line drawings" + (" with dimensions" if any_numbers else "")
                       + (f" from {len(ortho_views)} named views" if ortho_views else ""))
        return Classification(Mode.DIMENSIONED, 0.85 if any_numbers else 0.7, reasons, feats, exact, purpose)
    f = feats[0] if feats else None
    if f is None:
        return Classification(Mode.PARAMETRIC, 0.2, ["no usable reference"], feats, exact, purpose)
    if f.colors <= 6 and f.flatness > 0.8 and f.shading < 0.25:
        reasons.append(f"{f.colors} flat colour(s) with crisp edges — a logo, icon or line art")
        return Classification(Mode.VECTOR, 0.85, reasons, feats, exact, purpose)
    if f.line_art:
        reasons.append("line art without dimensions")
        return Classification(Mode.VECTOR, 0.6, reasons, feats, exact, purpose)
    if f.symmetry > 0.85:
        reasons.append(f"shaded object, mirror-symmetric ({f.symmetry:.2f}) — built from parametric parts")
        return Classification(Mode.PARAMETRIC, 0.7, reasons, feats, exact, purpose)
    reasons.append(f"shaded, irregular form (symmetry {f.symmetry:.2f})")
    return Classification(Mode.ORGANIC, 0.55, reasons, feats, exact, purpose)
