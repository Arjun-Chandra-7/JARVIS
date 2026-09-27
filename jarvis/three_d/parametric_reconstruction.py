"""Products and technical drawings → named, parametric parts.

Two engines:

**Orthographic / dimensioned** (``from_drawings``). Each view is a line drawing. The object's
outline is the largest group of strokes; dimension lines and text sit outside it and are not
geometry. Closed regions inside the outline are fitted as rectangles or circles; the stroke width
is measured from the drawing itself, so region sizes are corrected to the lines' outer edges.
Labels read locally become the scale. Parts are then matched across views — a front rectangle,
a side rectangle and a top circle with the same extents are one cylinder — and identical parts
placed symmetrically become one part with a Mirror modifier; three or more evenly spaced ones
become an Array. Units are never assumed: numbers with no unit and no unit note produce a
question.

**Single view** (``from_single_view``). One picture of a product. A mirror-symmetric silhouette
is reconstructed as a solid of revolution (the hidden depth is *assumed* round — estimated); an
asymmetric one as an extruded outline whose thickness is *invented*. Absolute size comes only
from a stated measurement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import calibration, imaging
from .types import (CameraEstimate, DetectedDimension, Evidence, EvidenceView, Fidelity, Material, ModelPart,
                    StudioModel, ViewKind)

TOL_MM = 1.0
DEFAULT_SIZE_MM = 150.0


class NeedsInput(Exception):
    """Reconstruction cannot honestly continue without one answer from the user."""

    def __init__(self, question: str, missing: list[str]) -> None:
        super().__init__(question)
        self.question = question
        self.missing = missing


# ============================================================================ drawings
@dataclass
class Region:
    shape: str                    # rect | circle | other
    x0: float                     # mm, view coordinates (u right, v up), origin bottom-left
    x1: float
    y0: float
    y1: float
    area_px: int = 0
    used: bool = False

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0


@dataclass
class ViewParse:
    view: ViewKind
    regions: list
    bbox_px: tuple
    scale: float                  # mm per px
    size_mm: tuple
    dims: list = field(default_factory=list)
    silhouette: Optional[np.ndarray] = None   # filled object mask, cropped to bbox
    stroke_px: float = 0.0
    evidence: Optional[EvidenceView] = None


def _ink(rgb: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) < 128


def object_ink(ink: np.ndarray) -> tuple[np.ndarray, tuple]:
    """The strokes that belong to the object: the largest group, plus everything inside its box."""
    import cv2

    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    if n <= 1:
        raise ValueError("no drawing found")
    main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[main, :4]
    keep = np.zeros(n, bool)
    for i in range(1, n):
        sx, sy, sw, sh = stats[i, :4]
        if sx >= x - 1 and sy >= y - 1 and sx + sw <= x + w + 1 and sy + sh <= y + h + 1:
            keep[i] = True
    return keep[labels], (int(x), int(y), int(x + w), int(y + h))


def regions_px(obj: np.ndarray, box: tuple) -> tuple[list, float]:
    """Closed regions inside the outline: (shape, x0, y0, x1, y1 continuous px, area), and the edge gap."""
    import cv2

    x0, y0, x1, y1 = box
    sub = ~obj[y0:y1, x0:x1]
    n, labels, stats, _ = cv2.connectedComponentsWithStats(sub.astype(np.uint8), 4)
    H, W = sub.shape
    out = []
    for i in range(1, n):
        sx, sy, sw, sh, area = stats[i]
        if sx == 0 or sy == 0 or sx + sw >= W or sy + sh >= H:
            continue                              # the outside, or a gap touching the border
        if area < max(30, 0.0005 * W * H):
            continue
        comp = (labels[sy:sy + sh, sx:sx + sw] == i)
        filled = imaging.filled_silhouette(comp)
        fill_ratio = filled.sum() / float(sw * sh)
        contours, _ = cv2.findContours(filled.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        perim = max(1.0, sum(cv2.arcLength(c, True) for c in contours))
        circ = 4 * math.pi * filled.sum() / perim ** 2
        if fill_ratio > 0.95:
            shape = "rect"
        elif abs(sw - sh) <= max(2, 0.04 * max(sw, sh)) and 0.72 < fill_ratio < 0.84 and circ > 0.8:
            shape = "circle"
        else:
            shape = "other"
        out.append((shape, x0 + sx, y0 + sy, x0 + sx + sw, y0 + sy + sh, int(area)))
    if not out:
        return out, 0.0
    # The gap between the outline's outer edge and the nearest region interior is the stroke,
    # as this drawing drew it. Measured on all four sides; the median is robust to one odd side.
    gaps = [min(r[1] for r in out) - x0, x1 - max(r[3] for r in out),
            min(r[2] for r in out) - y0, y1 - max(r[4] for r in out)]
    return out, float(np.median(gaps))


def parse_view(path: str, view: ViewKind, words: Optional[list] = None, unit: Optional[str] = None) -> ViewParse:
    """Read one orthographic drawing. ``words`` are OCR words (local); only numbers survive."""
    rgb = imaging.load_rgb(path, 3000)
    ink = _ink(rgb)
    obj, box = object_ink(ink)
    raw, gap = regions_px(obj, box)
    labels, note = calibration.dimension_labels(words or [])
    resolved = calibration.resolve_units(labels, note, unit)
    if labels and resolved is None:
        raise NeedsInput("The drawing's numbers have no units. Are they millimetres, centimetres or inches?",
                         ["units"])
    dims = calibration.pair_dimensions(labels, box, resolved or "mm") if labels else []
    scale, agree = calibration.scale_from_dimensions(dims)
    x0, y0, x1, y1 = box
    sil = imaging.filled_silhouette(obj[y0:y1, x0:x1])
    ev = EvidenceView(reference_id="", view=view, scale_mm_per_px=scale, scale_confidence=agree,
                      dimensions=dims, silhouette_px=float(sil.sum()),
                      camera=calibration.orthographic(view, 0.0), confidence=0.9 if dims else 0.5)
    regions = []
    for shape, rx0, ry0, rx1, ry1, area in raw:
        # continuous px → mm, extended by the stroke gap, origin at the outline's bottom-left
        u0, u1 = (rx0 - gap - x0), (rx1 + gap - x0)
        v0, v1 = (y1 - (ry1 + gap)), (y1 - (ry0 - gap))
        regions.append(Region(shape, u0, u1, v0, v1, area))
    return ViewParse(view, regions, box, scale, ((x1 - x0), (y1 - y0)), dims, sil, gap, ev)


def _apply_scale(vp: ViewParse, s: float) -> None:
    for r in vp.regions:
        r.x0, r.x1, r.y0, r.y1 = r.x0 * s, r.x1 * s, r.y0 * s, r.y1 * s
    vp.size_mm = (vp.size_mm[0] * s, vp.size_mm[1] * s)
    vp.scale = s


def _close(a, b, tol=TOL_MM):
    return abs(a - b) <= max(tol, 0.015 * max(abs(a), abs(b)))


def _labelled(value: float, labels: set) -> bool:
    return any(abs(value - l) <= 0.35 for l in labels)


@dataclass
class Solid:
    kind: str                     # box | cylinder
    x: tuple
    y: tuple
    z: tuple
    score: float


def match_views(front: ViewParse, side: ViewParse, top: ViewParse) -> tuple[list[Solid], list[str]]:
    """Front (x,z) + side (y,z) + top (x,y) regions → solids. Returns solids and unexplained regions."""
    solids, unexplained = [], []
    for f in front.regions:
        best = None
        for s in side.regions:
            if not (_close(f.y0, s.y0) and _close(f.y1, s.y1)):
                continue
            for t in top.regions:
                if not (_close(f.x0, t.x0) and _close(f.x1, t.x1)):
                    continue
                if not (_close(s.x0, t.y0) and _close(s.x1, t.y1)):
                    continue
                score = (abs(f.y0 - s.y0) + abs(f.y1 - s.y1) + abs(f.x0 - t.x0) + abs(f.x1 - t.x1)
                         + abs(s.x0 - t.y0) + abs(s.x1 - t.y1))
                if best is None or score < best[0]:
                    best = (score, s, t)
        if best is None:
            unexplained.append(f"front region {f.w:.0f}×{f.h:.0f} mm has no match in the side and top views")
            continue
        _, s, t = best
        kind = "cylinder" if t.shape == "circle" else "box"
        # Average the evidence from the two views that see each axis.
        solids.append(Solid(kind, ((f.x0 + t.x0) / 2, (f.x1 + t.x1) / 2), ((s.x0 + t.y0) / 2, (s.x1 + t.y1) / 2),
                            ((f.y0 + s.y0) / 2, (f.y1 + s.y1) / 2), best[0]))
        s.used = t.used = f.used = True
    # Remove exact duplicates (a region matched twice through identical side projections is fine;
    # two identical solids at one place are not).
    uniq = []
    for so in solids:
        if not any(all(_close(a, b, 0.2) for a, b in zip(so.x + so.y + so.z, u.x + u.y + u.z)) for u in uniq):
            uniq.append(so)
    return uniq, unexplained


def _name_parts(solids: list[Solid]) -> list[tuple[str, Solid]]:
    """Semantic names from role in the assembly: the widest lowest solid is the base, etc."""
    named = []
    if not solids:
        return named
    base = max(solids, key=lambda s: ((s.x[1] - s.x[0]) * (s.y[1] - s.y[0]), -s.z[0]))
    counters: dict[str, int] = {}
    for so in solids:
        if so is base:
            named.append(("Base", so))
            continue
        tall = (so.z[1] - so.z[0]) > 1.5 * max(so.x[1] - so.x[0], so.y[1] - so.y[0])
        kind = ("Post" if tall else "Boss") if so.kind == "cylinder" else ("Pillar" if tall else "Block")
        counters[kind] = counters.get(kind, 0) + 1
        named.append((f"{kind} {counters[kind]}", so))
    # single-member kinds lose their number
    totals = {}
    for n, _ in named:
        k = n.rsplit(" ", 1)[0] if n[-1].isdigit() else n
        totals[k] = totals.get(k, 0) + 1
    return [(n.rsplit(" ", 1)[0] if n[-1].isdigit() and totals[n.rsplit(" ", 1)[0]] == 1 else n, s) for n, s in named]


def _group_repeats(named: list, cx: float) -> tuple[list, list, list]:
    """Pairs mirrored about the object's centre → one part + Mirror; ≥3 evenly spaced → Array."""
    used, mirrors, arrays = set(), [], []
    items = [(n, s) for n, s in named if n != "Base"]
    for i, (n1, s1) in enumerate(items):
        if n1 in used:
            continue
        same = [(n, s) for n, s in items if n not in used and s.kind == s1.kind
                and all(_close(a[1] - a[0], b[1] - b[0], 0.3) for a, b in ((s.x, s1.x), (s.y, s1.y), (s.z, s1.z)))
                and _close(s.y[0], s1.y[0], 0.3) and _close(s.z[0], s1.z[0], 0.3)]
        if len(same) == 2:
            (na, sa), (nb, sb) = sorted(same, key=lambda t: t[1].x[0])
            ca, cb = (sa.x[0] + sa.x[1]) / 2, (sb.x[0] + sb.x[1]) / 2
            if _close((ca + cb) / 2, cx, 0.5):
                mirrors.append((na, nb))
                used.update({na, nb})
        elif len(same) >= 3:
            same.sort(key=lambda t: t[1].x[0])
            xs = [(s.x[0] + s.x[1]) / 2 for _, s in same]
            steps = np.diff(xs)
            if np.all(np.abs(steps - steps.mean()) < 0.5):
                arrays.append(([n for n, _ in same], float(steps.mean())))
                used.update(n for n, _ in same)
    return mirrors, arrays, list(used)


def from_drawings(views: dict, words: Optional[dict] = None, *, unit: Optional[str] = None,
                  known: Optional[dict] = None, name: str = "Part", exact: bool = False) -> tuple[StudioModel, dict]:
    """Front/side/top drawings → a dimensioned parametric model.

    ``views``: {"front": path, "side": path, "top": path}. ``words``: OCR words per view.
    ``known``: dimensions the user said, e.g. {"width": 120}. Raises NeedsInput when a critical
    fact is missing (units, any scale at all when exact dimensions were asked for, a view).
    """
    words = words or {}
    known = known or {}
    missing = [v for v in ("front", "side", "top") if v not in views]
    if missing:
        raise NeedsInput(f"I need the {' and '.join(missing)} view{'s' if len(missing) > 1 else ''} as well "
                         "to build this to its dimensions.", missing)
    parsed = {v: parse_view(views[v], ViewKind(v), words.get(v), unit) for v in ("front", "side", "top")}
    labels: set = set()
    for vp in parsed.values():
        labels |= {round(d.value_mm, 3) for d in vp.dims}
    # Scale per view; a view without labels borrows from a shared axis of a labelled view.
    shared = {"front": ("x", "z"), "side": ("y", "z"), "top": ("x", "y")}
    axis_mm: dict[str, float] = {}
    for v, vp in parsed.items():
        if vp.scale:
            axis_mm[shared[v][0]] = vp.size_mm[0] * vp.scale
            axis_mm[shared[v][1]] = vp.size_mm[1] * vp.scale
    for key, axis in (("width", "x"), ("depth", "y"), ("height", "z")):
        if key in known:
            axis_mm[axis] = float(known[key])
            labels.add(round(float(known[key]), 3))
    scale_ev = Evidence.VERIFIED if labels else Evidence.ESTIMATED
    if not axis_mm:
        if exact:
            raise NeedsInput("The drawings have no dimensions I can read. What is one real measurement — "
                             "the overall width, for example?", ["scale"])
        axis_mm["x"] = DEFAULT_SIZE_MM
    for v, vp in parsed.items():
        if not vp.scale:
            a, b = shared[v]
            if a in axis_mm:
                vp.scale = axis_mm[a] / vp.size_mm[0]
            elif b in axis_mm:
                vp.scale = axis_mm[b] / vp.size_mm[1]
        _apply_scale(vp, vp.scale)
    solids, unexplained = match_views(parsed["front"], parsed["side"], parsed["top"])
    if not solids:
        raise NeedsInput("I couldn't match the three views to one object. Are they all of the same part?",
                         ["consistent views"])
    ox = (min(s.x[0] for s in solids) + max(s.x[1] for s in solids)) / 2
    oy = (min(s.y[0] for s in solids) + max(s.y[1] for s in solids)) / 2
    named = _name_parts(solids)
    mirrors, arrays, grouped = _group_repeats(named, ox)
    steel = Material("Machined aluminium", [0.78, 0.79, 0.8], metallic=1.0, roughness=0.35,
                     evidence=Evidence.INVENTED)
    parts: list[ModelPart] = []
    skip = set()
    for na, nb in mirrors:
        skip.add(nb)
    for names, _ in arrays:
        skip.update(names[1:])

    def axis_ev(values):
        evs = [Evidence.VERIFIED if _labelled(v, labels) else (Evidence.CONSTRAINED if scale_ev == Evidence.VERIFIED
                                                               else Evidence.ESTIMATED) for v in values]
        return Evidence.weakest(evs), evs

    for n, so in named:
        if n in skip:
            continue
        dx, dy, dz = so.x[1] - so.x[0], so.y[1] - so.y[0], so.z[1] - so.z[0]
        loc = [round((so.x[0] + so.x[1]) / 2 - ox, 3), round((so.y[0] + so.y[1]) / 2 - oy, 3), round((so.z[0] + so.z[1]) / 2, 3)]
        overall, per = axis_ev([dx, dy, dz])
        if so.kind == "cylinder":
            params = {"radius": round((dx + dy) / 4, 3), "height": round(dz, 3), "segments": 64}
            geometry = "cylinder"
        else:
            params = {"x": round(dx, 3), "y": round(dy, 3), "z": round(dz, 3)}
            geometry = "box"
        params["evidence"] = {"x": per[0].value, "y": per[1].value, "z": per[2].value}
        part = ModelPart(name=n, geometry=geometry, params=params, location=loc, material=steel.name,
                         evidence=overall, confidence=0.9 if overall != Evidence.ESTIMATED else 0.6,
                         aliases=[n.split()[0].lower()])
        for na, nb in mirrors:
            if n == na:
                part.modifiers.append({"type": "mirror", "axis": "x", "about": "Base"})
                part.params["mirrored"] = nb
                part.aliases += ["pair", "blocks" if "Block" in n else n.split()[0].lower() + "s"]
        for names, step in arrays:
            if n == names[0]:
                part.modifiers.append({"type": "array", "count": len(names), "offset": [step, 0.0, 0.0]})
                part.params["repeated"] = len(names)
        parts.append(part)
    base = next((p for p in parts if p.name == "Base"), None)
    if base:
        for p in parts:
            if p is not base:
                p.parent = "Base"
                base.children.append(p.name)
    size = [max(s.x[1] for s in solids) - min(s.x[0] for s in solids),
            max(s.y[1] for s in solids) - min(s.y[0] for s in solids),
            max(s.z[1] for s in solids) - min(s.z[0] for s in solids)]
    model = StudioModel(name=name, parts=parts, materials=[steel], category="machined part",
                        fidelity=Fidelity.DIMENSIONAL if scale_ev == Evidence.VERIFIED else Fidelity.VISUAL,
                        symmetry=["x"] if mirrors else [],
                        repeats=[{"parts": [a, b], "kind": "mirror"} for a, b in mirrors]
                        + [{"parts": ns, "kind": "array", "spacing": st} for ns, st in arrays],
                        cameras=[calibration.orthographic(ViewKind(v), 0.0) for v in ("front", "side", "top")])
    facts = {"views": parsed, "labels": sorted(labels), "size_mm": size, "unexplained": unexplained,
             "origin": (ox, oy)}
    return model, facts


# ============================================================================ one picture
def revolve_profile(mask: np.ndarray, samples: int = 48) -> tuple[list, float, int]:
    """Half-widths of a symmetric silhouette, bottom to top: [(r_px, z_px)], axis column, rows."""
    ys = np.nonzero(mask.any(axis=1))[0]
    y_top, y_bot = int(ys.min()), int(ys.max())
    rows = np.linspace(y_bot, y_top, samples).round().astype(int)
    centres, prof = [], []
    for y in rows:
        xs = np.nonzero(mask[y])[0]
        if len(xs) == 0:
            continue
        centres.append((xs.min() + xs.max() + 1) / 2)
        prof.append(((xs.max() + 1 - xs.min()) / 2, float(y_bot + 1 - y)))
    axis = float(np.median(centres)) if centres else 0.0
    return prof, axis, y_bot - y_top + 1


def from_single_view(path: str, *, known: Optional[dict] = None, name: str = "Object",
                     exact: bool = False) -> tuple[StudioModel, dict]:
    """One perspective picture → a visually matched approximation. Never 'exact'."""
    known = known or {}
    rgb = imaging.load_rgb(path, 1600)
    mask = imaging.filled_silhouette(imaging.foreground_mask(rgb))
    b = imaging.bbox(mask)
    if b is None:
        raise ValueError("no object found")
    x0, y0, x1, y1 = b
    crop = mask[y0:y1, x0:x1]
    w_px, h_px = x1 - x0, y1 - y0
    if exact and not known:
        raise NeedsInput("One picture can't give exact depth or size. Can you show me the side view, "
                         "or tell me its depth?", ["side view or depth measurement"])
    if "height" in known:
        s, scale_ev = known["height"] / h_px, Evidence.CONSTRAINED
    elif "width" in known or "diameter" in known:
        s, scale_ev = known.get("width", known.get("diameter")) / w_px, Evidence.CONSTRAINED
    else:
        s, scale_ev = DEFAULT_SIZE_MM / h_px, Evidence.ESTIMATED
    sym = imaging.mirror_symmetry(crop)
    pal = imaging.quantize(rgb, mask, k=3)
    color = [round(c / 255.0, 4) for c in pal.colors[0]] if len(pal.colors) else [0.7, 0.7, 0.7]
    mat = Material(f"{name} surface", color, roughness=0.45, evidence=Evidence.ESTIMATED)
    cam = calibration.estimate_camera(crop, round_top=sym > 0.9)
    facts = {"mask": crop, "scale_mm_per_px": s, "symmetry": sym, "camera": cam}
    if sym > 0.9:
        prof_px, _axis, _ = revolve_profile(crop)
        profile = [[0.0, 0.0]] + [[round(r * s, 3), round(z * s, 3)] for r, z in prof_px] + \
                  [[0.0, round(prof_px[-1][1] * s, 3)]]
        part = ModelPart(name=name, geometry="revolve",
                         params={"profile": profile, "segments": 96,
                                 "evidence": {"silhouette": Evidence.ESTIMATED.value, "scale": scale_ev.value,
                                              "depth": Evidence.ESTIMATED.value},
                                 "assumption": "rotationally symmetric (the hidden side is assumed round)"},
                         location=[0.0, 0.0, 0.0], material=mat.name, evidence=Evidence.ESTIMATED, confidence=0.55,
                         aliases=["body", "vase", "bottle", "shape"])
        facts["kind"] = "revolve"
    else:
        from .vector_reconstruction import trace_layer, _to_mm
        polys = trace_layer(crop, 1.0, 20)
        cx, cy = w_px / 2, h_px / 2
        depth = round(min(w_px, h_px) * s * 0.35, 2)
        part = ModelPart(name=name, geometry="curve_extrude",
                         params={"polygons": [{"outer": _to_mm(p["outer"], cx, cy, s),
                                               "holes": [_to_mm(h, cx, cy, s) for h in p["holes"]]} for p in polys],
                                 "depth": depth, "bevel": round(depth * 0.2, 3), "width": w_px * s, "height": h_px * s,
                                 "evidence": {"outline": Evidence.ESTIMATED.value, "scale": scale_ev.value,
                                              "depth": Evidence.INVENTED.value}},
                         location=[0.0, 0.0, round(h_px * s / 2, 3)], rotation=[90.0, 0.0, 0.0], material=mat.name,
                         evidence=Evidence.INVENTED, confidence=0.35, aliases=["body", "shape"])
        facts["kind"] = "extrude"
    model = StudioModel(name=name, parts=[part], materials=[mat], category="product", fidelity=Fidelity.VISUAL,
                        symmetry=["z-axis"] if facts["kind"] == "revolve" else [], cameras=[cam])
    return model, facts


def refine_revolve(part: ModelPart, ref_crop: np.ndarray, render_crop: np.ndarray, gain: float = 0.8) -> float:
    """One refinement step: scale each profile radius by how much wider the reference is at that height.

    Both crops are compared at the reference's height (shape alignment). Returns the mean
    absolute relative width error before the step, so the caller can watch it fall.
    """
    import cv2

    H = ref_crop.shape[0]
    ren = cv2.resize(render_crop.astype(np.uint8), (max(1, round(render_crop.shape[1] * H / render_crop.shape[0])), H),
                     interpolation=cv2.INTER_NEAREST).astype(bool)
    prof = part.params["profile"]
    zmax = max(z for _, z in prof) or 1.0
    errs = []
    new = [prof[0]]
    for r, z in prof[1:-1]:
        row = int(round((1 - z / zmax) * (H - 1)))
        row = min(max(row, 0), H - 1)
        wr, wm = ref_crop[row].sum(), ren[row].sum()
        if wm > 0 and wr > 0:
            ratio = wr / wm
            errs.append(abs(ratio - 1))
            r = r * (1 + gain * (ratio - 1))
        new.append([round(max(r, 0.0), 4), z])
    new.append(prof[-1])
    part.params["profile"] = new
    return float(np.mean(errs)) if errs else 0.0
