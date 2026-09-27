"""``TeachingVisualRequest``: what the Study Companion asks the teaching overlay to draw.

The overlay (``jarvis/teach``, ``overlay/teach``) is shared code and is not edited here. This
module defines the request the companion produces — diagram type, objects, labels, equations,
steps with narration, highlights, the animation sequence, whether it follows the voice, and when
it clears — plus:

* ``validate`` — structural rules (unique ids, every reference resolves, bounded counts and text,
  coordinates on the canvas) **and** subject rules: a ray diagram must have a normal, the
  refracted ray must bend towards the normal going into a denser medium, and so on.
* builders for the common Class 10 diagrams;
* ``FakeRenderer`` for tests;
* ``to_overlay_batch`` — the integration adapter: turns a request into an overlay command batch
  and runs it through the overlay's own validator (``jarvis.teach.protocol.validate``), so the
  contract is tested against the real protocol today, without touching the overlay.

Coordinates are on an abstract 1000 × 600 canvas; the adapter scales them into the region the
overlay gives it.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from .types import _Str

CANVAS_W, CANVAS_H = 1000.0, 600.0
MAX_OBJECTS, MAX_STEPS, MAX_TEXT = 60, 12, 120
_ID = re.compile(r"^[a-z0-9_.-]{1,40}$")
_UNSAFE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|javascript:|data:|file:|<\s*/?\s*[a-z!?]|/home/|\\\\)")


class Diagram(_Str):
    RAY = "ray_diagram"
    CIRCUIT = "circuit"
    TRIANGLE = "triangle"
    COORDINATE = "coordinate_plane"
    NUMBER_LINE = "number_line"
    FLOW = "flow"                    # biology processes, cause→effect chains, RAG pipelines
    TABLE = "table"


@dataclass
class VisualObject:
    id: str
    kind: str                        # "line" | "arrow" | "text" | "rect" | "circle" | "node" | "edge" | "equation"
    points: list[list[float]] = field(default_factory=list)   # [[x, y], …] — endpoints / position
    text: str = ""
    size: list[float] = field(default_factory=list)           # [w, h] for rect/node, [r] for circle
    ends: list[str] = field(default_factory=list)             # edge: [from_id, to_id]
    role: str = ""                   # semantic role checked by subject rules: "normal", "incident", …
    style: str = "primary"           # overlay colour token
    dashed: bool = False


@dataclass
class VisualStep:
    id: str
    narration: str                   # the sentence spoken while this step draws
    show: list[str] = field(default_factory=list)
    highlight: list[str] = field(default_factory=list)
    ms: int = 900


@dataclass
class TeachingVisualRequest:
    diagram: Diagram
    title: str
    objects: list[VisualObject]
    steps: list[VisualStep]
    equations: list[str] = field(default_factory=list)
    language: str = "en"
    speech_sync: bool = True         # each step waits for its narration's audio
    hold_s: int = 150                # overlay clears after this long without updates
    dismiss_on: list[str] = field(default_factory=lambda: ["clear", "next_topic", "session_end"])
    params: dict = field(default_factory=dict)                # builder inputs, for subject checks

    def object(self, oid: str) -> Optional[VisualObject]:
        return next((o for o in self.objects if o.id == oid), None)

    def labels(self) -> list[str]:
        return [o.text for o in self.objects if o.kind == "text" and o.text]

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------------------------ validation
def validate(req: TeachingVisualRequest) -> list[str]:
    errs: list[str] = []
    ids = [o.id for o in req.objects]
    if len(set(ids)) != len(ids):
        errs.append("duplicate_object_id")
    if not 1 <= len(req.objects) <= MAX_OBJECTS:
        errs.append("object_count")
    if not 1 <= len(req.steps) <= MAX_STEPS:
        errs.append("step_count")
    if not 5 <= req.hold_s <= 3600:
        errs.append("hold_s")
    for o in req.objects:
        if not _ID.match(o.id):
            errs.append(f"bad_id:{o.id[:20]}")
        for p in o.points:
            if len(p) != 2 or not (0 <= p[0] <= CANVAS_W and 0 <= p[1] <= CANVAS_H):
                errs.append(f"off_canvas:{o.id}")
                break
        if len(o.text) > MAX_TEXT or _UNSAFE.search(o.text):
            errs.append(f"unsafe_text:{o.id}")
        if o.kind in ("line", "arrow") and len(o.points) != 2:
            errs.append(f"needs_two_points:{o.id}")
        if o.kind == "edge" and (len(o.ends) != 2 or any(e not in ids for e in o.ends)):
            errs.append(f"dangling_edge:{o.id}")
    seen: set[str] = set()
    for s in req.steps:
        if len(s.narration) > 300 or _UNSAFE.search(s.narration):
            errs.append(f"unsafe_narration:{s.id}")
        for ref in s.show + s.highlight:
            if ref not in ids:
                errs.append(f"unknown_ref:{s.id}:{ref}")
        seen.update(s.show)
    never_shown = [i for i in ids if i not in seen]
    if never_shown:
        errs.append("never_shown:" + ",".join(never_shown[:5]))
    rule = _SUBJECT_RULES.get(req.diagram)
    if rule:
        errs += rule(req)
    return errs


def _angle_from_normal(ray: VisualObject, normal: VisualObject, towards_boundary: bool) -> float:
    (x1, y1), (x2, y2) = ray.points
    dx, dy = (x2 - x1, y2 - y1)
    (nx1, ny1), (nx2, ny2) = normal.points
    nxv, nyv = (nx2 - nx1, ny2 - ny1)
    cos = abs(dx * nxv + dy * nyv) / (math.hypot(dx, dy) * math.hypot(nxv, nyv))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _ray_rules(req: TeachingVisualRequest) -> list[str]:
    errs = []
    by_role = {o.role: o for o in req.objects if o.role}
    for need in ("boundary", "normal", "incident", "refracted"):
        if need not in by_role:
            errs.append(f"ray:missing_{need}")
    if errs:
        return errs
    b, n, inc, ref = by_role["boundary"], by_role["normal"], by_role["incident"], by_role["refracted"]
    # The normal is perpendicular to the boundary.
    bx, by_ = b.points[1][0] - b.points[0][0], b.points[1][1] - b.points[0][1]
    nx, ny = n.points[1][0] - n.points[0][0], n.points[1][1] - n.points[0][1]
    if abs(bx * nx + by_ * ny) > 1e-6 * math.hypot(bx, by_) * math.hypot(nx, ny) + 1e-6:
        errs.append("ray:normal_not_perpendicular")
    # The incident ray ends where the refracted ray starts (the point of incidence).
    if math.dist(inc.points[1], ref.points[0]) > 1.0:
        errs.append("ray:rays_not_joined")
    i = _angle_from_normal(inc, n, True)
    r = _angle_from_normal(ref, n, False)
    n1, n2 = req.params.get("n1", 1.0), req.params.get("n2", 1.5)
    if n2 > n1 and not r < i:
        errs.append("ray:should_bend_towards_normal")
    if n2 < n1 and not r > i:
        errs.append("ray:should_bend_away_from_normal")
    expected_r = math.degrees(math.asin(min(1.0, n1 / n2 * math.sin(math.radians(i)))))
    if abs(r - expected_r) > 1.5:
        errs.append("ray:angle_does_not_follow_snell")
    labels = " ".join(req.labels()).lower()
    for word in ("normal", "incident", "refracted"):
        if word not in labels:
            errs.append(f"ray:label_missing_{word}")
    return errs


def _circuit_rules(req: TeachingVisualRequest) -> list[str]:
    roles = {o.role for o in req.objects}
    errs = [f"circuit:missing_{r}" for r in ("cell", "current_arrow") if r not in roles]
    arrow = next((o for o in req.objects if o.role == "current_arrow"), None)
    plus = next((o for o in req.objects if o.role == "cell_plus"), None)
    if arrow and plus and math.dist(arrow.points[0], plus.points[0]) > math.dist(arrow.points[1], plus.points[0]):
        errs.append("circuit:conventional_current_must_leave_positive_terminal")
    return errs


def _triangle_rules(req: TeachingVisualRequest) -> list[str]:
    a, b = req.params.get("a"), req.params.get("b")
    hyp = next((o for o in req.objects if o.role == "hypotenuse"), None)
    if a is None or b is None or not hyp:
        return ["triangle:missing_sides"]
    return [] if f"{math.hypot(a, b):.2f}".rstrip("0").rstrip(".") in hyp.text or "√" in hyp.text else ["triangle:hypotenuse_label_wrong"]


_SUBJECT_RULES = {Diagram.RAY: _ray_rules, Diagram.CIRCUIT: _circuit_rules, Diagram.TRIANGLE: _triangle_rules}


# ------------------------------------------------------------------------------------ builders
def _txt(id, x, y, text, style="text"):
    return VisualObject(id, "text", [[x, y]], text=text, style=style)


def ray_diagram(n1: float = 1.0, n2: float = 1.5, incidence_deg: float = 40.0, *, media: tuple[str, str] = ("Air", "Glass"),
                language: str = "en") -> TeachingVisualRequest:
    """Refraction at a plane boundary, drawn with the real Snell's-law angle."""
    ox, oy, L = 500.0, 300.0, 220.0
    i = math.radians(incidence_deg)
    s = min(1.0, n1 / n2 * math.sin(i))
    r = math.asin(s)
    inc_start = [ox - L * math.sin(i), oy - L * math.cos(i)]
    ref_end = [ox + L * math.sin(r), oy + L * math.cos(r)]
    objs = [
        VisualObject("boundary", "line", [[150, oy], [850, oy]], role="boundary", style="muted"),
        _txt("medium1", 170, oy - 30, media[0]), _txt("medium2", 170, oy + 40, media[1]),
        VisualObject("normal", "line", [[ox, oy - 260], [ox, oy + 260]], role="normal", style="muted", dashed=True),
        _txt("normal_label", ox + 12, 60, "Normal"),
        VisualObject("incident", "arrow", [inc_start, [ox, oy]], role="incident"),
        _txt("incident_label", inc_start[0] - 20, inc_start[1] - 14, "Incident ray"),
        VisualObject("refracted", "arrow", [[ox, oy], ref_end], role="refracted", style="confirm"),
        _txt("refracted_label", ref_end[0] + 10, ref_end[1], "Refracted ray"),
        _txt("angle_i", ox - 48, oy - 70, "∠i", style="attention"),
        _txt("angle_r", ox + 18, oy + 80, "∠r", style="attention"),
    ]
    towards = n2 > n1
    steps = [
        VisualStep("s1", "Here is the boundary between the two media.", ["boundary", "medium1", "medium2"]),
        VisualStep("s2", "Draw the normal: a line perpendicular to the boundary at the point of incidence.",
                   ["normal", "normal_label"]),
        VisualStep("s3", "The incident ray arrives at an angle i to the normal.", ["incident", "incident_label", "angle_i"],
                   ["angle_i"]),
        VisualStep("s4", "Inside the denser medium light slows down, so it bends towards the normal." if towards else
                   "Entering the rarer medium light speeds up, so it bends away from the normal.",
                   ["refracted", "refracted_label", "angle_r"], ["angle_r"]),
    ]
    return TeachingVisualRequest(Diagram.RAY, "Refraction of light", objs, steps,
                                 equations=["n₁ sin i = n₂ sin r"], language=language,
                                 params={"n1": n1, "n2": n2, "i": incidence_deg, "r": round(math.degrees(r), 2)})


def circuit(show_electrons: bool = True) -> TeachingVisualRequest:
    """A cell and a bulb in a loop: conventional current + → −, electrons the other way."""
    objs = [
        VisualObject("wire", "rect", [[250, 150]], size=[500, 300], role="wire", style="muted"),
        VisualObject("cell", "rect", [[470, 430]], size=[60, 40], role="cell"),
        _txt("plus", 545, 455, "+"), _txt("minus", 445, 455, "−"),
        VisualObject("plus_pt", "circle", [[530, 450]], size=[4], role="cell_plus", style="attention"),
        VisualObject("bulb", "circle", [[500, 150]], size=[26], role="bulb"),
        VisualObject("current", "arrow", [[560, 450], [750, 300]], role="current_arrow", style="primary"),
        _txt("current_label", 690, 400, "Conventional current (+ → −)"),
    ]
    steps = [VisualStep("s1", "A cell and a bulb joined in a loop.", ["wire", "cell", "plus", "minus", "plus_pt", "bulb"]),
             VisualStep("s2", "Conventional current leaves the positive terminal and goes round to the negative.",
                        ["current", "current_label"], ["current"])]
    if show_electrons:
        objs += [VisualObject("electrons", "arrow", [[250, 300], [440, 450]], role="electron_arrow", style="attention",
                              dashed=True),
                 _txt("electrons_label", 180, 400, "Electrons (− → +)")]
        steps.append(VisualStep("s3", "Electrons are negative, so they drift the opposite way.", ["electrons", "electrons_label"],
                                ["electrons"]))
    return TeachingVisualRequest(Diagram.CIRCUIT, "Current and electron flow", objs, steps)


def right_triangle(a: float, b: float, unit: str = "cm", hyp_text: str = "") -> TeachingVisualRequest:
    k = 360 / max(a, b)
    A, B, C = [300, 500], [300 + b * k, 500], [300, 500 - a * k]
    h = math.hypot(a, b)
    objs = [VisualObject("side_a", "line", [A, C], role="leg"), VisualObject("side_b", "line", [A, B], role="leg"),
            VisualObject("hyp", "line", [C, B], role="hyp_line", style="attention"),
            _txt("label_a", A[0] - 60, (A[1] + C[1]) / 2, f"{a:g} {unit}"),
            _txt("label_b", (A[0] + B[0]) / 2, A[1] + 30, f"{b:g} {unit}"),
            VisualObject("label_h", "text", [[(C[0] + B[0]) / 2 + 20, (C[1] + B[1]) / 2 - 10]],
                         text=hyp_text or f"√({a:g}² + {b:g}²) ≈ {h:.2f} {unit}", role="hypotenuse", style="confirm")]
    steps = [VisualStep("s1", "The two sides we know.", ["side_a", "side_b", "label_a", "label_b"]),
             VisualStep("s2", "The hypotenuse is opposite the right angle.", ["hyp"], ["hyp"]),
             VisualStep("s3", "Square, add, then take the square root.", ["label_h"], ["label_h"])]
    return TeachingVisualRequest(Diagram.TRIANGLE, "Pythagoras theorem", objs, steps,
                                 equations=[f"h² = {a:g}² + {b:g}²"], params={"a": a, "b": b})


def coordinate_plane(points: dict[str, tuple[float, float]], span: float = 10) -> TeachingVisualRequest:
    cx, cy, k = 500.0, 300.0, 260.0 / span
    objs = [VisualObject("x_axis", "arrow", [[200, cy], [800, cy]], style="muted"),
            VisualObject("y_axis", "arrow", [[cx, 580], [cx, 20]], style="muted")]
    for name, (x, y) in points.items():
        pid = f"pt_{name.lower()}"
        objs.append(VisualObject(pid, "circle", [[cx + x * k, cy - y * k]], size=[5], style="attention"))
        objs.append(_txt(pid + "_l", cx + x * k + 8, cy - y * k - 8, f"{name}({x:g}, {y:g})"))
    steps = [VisualStep("s1", "The axes.", ["x_axis", "y_axis"]),
             VisualStep("s2", "The points.", [o.id for o in objs[2:]])]
    return TeachingVisualRequest(Diagram.COORDINATE, "Coordinate plane", objs, steps)


def number_line(marks: list[float], highlight: Optional[float] = None) -> TeachingVisualRequest:
    lo, hi = min(marks + [0]), max(marks + [0])
    span = (hi - lo) or 1
    X = lambda v: 100 + (v - lo) / span * 800  # noqa: E731
    objs = [VisualObject("line", "arrow", [[80, 300], [920, 300]], style="muted")]
    for i, m in enumerate(marks):
        objs.append(_txt(f"m{i}", X(m), 330, f"{m:g}", style="attention" if m == highlight else "text"))
    steps = [VisualStep("s1", "The number line.", [o.id for o in objs])]
    return TeachingVisualRequest(Diagram.NUMBER_LINE, "Number line", objs, steps)


def flow(title: str, stages: list[str], narration: Optional[list[str]] = None) -> TeachingVisualRequest:
    """A left-to-right chain: reflex arc, photosynthesis inputs → outputs, a RAG pipeline."""
    n = max(1, len(stages))
    w = min(160.0, 800.0 / n - 20)
    objs, steps = [], []
    for i, s in enumerate(stages):
        x = 100 + i * (800.0 / n)
        objs.append(VisualObject(f"n{i}", "node", [[x, 260]], text=s[:MAX_TEXT], size=[w, 80]))
        show = [f"n{i}"]
        if i:
            objs.append(VisualObject(f"e{i}", "edge", ends=[f"n{i - 1}", f"n{i}"]))
            show.append(f"e{i}")
        steps.append(VisualStep(f"s{i + 1}", (narration or stages)[i][:300], show, [f"n{i}"]))
    return TeachingVisualRequest(Diagram.FLOW, title, objs, steps[:MAX_STEPS])


def table(title: str, header: list[str], rows: list[list[str]]) -> TeachingVisualRequest:
    objs, steps = [], []
    cw = 800.0 / max(1, len(header))
    for c, h in enumerate(header):
        objs.append(_txt(f"h{c}", 120 + c * cw, 80, h, style="primary"))
    steps.append(VisualStep("s0", "The table.", [o.id for o in objs]))
    for r, row in enumerate(rows[:8]):
        ids = []
        for c, cell in enumerate(row):
            objs.append(_txt(f"c{r}_{c}", 120 + c * cw, 130 + r * 50, cell))
            ids.append(f"c{r}_{c}")
        steps.append(VisualStep(f"s{r + 1}", " | ".join(row), ids, ids[-1:]))
    return TeachingVisualRequest(Diagram.TABLE, title, objs, steps[:MAX_STEPS])


# ------------------------------------------------------------------------------------ renderers
@dataclass
class FakeRenderer:
    """Records what would be drawn. The contract test's stand-in for the overlay."""
    shown: list[TeachingVisualRequest] = field(default_factory=list)
    cleared: int = 0
    rejected: list[list[str]] = field(default_factory=list)

    def render(self, req: TeachingVisualRequest) -> bool:
        errs = validate(req)
        if errs:
            self.rejected.append(errs)
            return False
        self.shown.append(req)
        return True

    def clear(self) -> None:
        self.cleared += 1


_OVERLAY_COLOR = {"primary", "confirm", "attention", "error", "text", "muted"}


def to_overlay_batch(req: TeachingVisualRequest, *, lesson: str = "study", region: tuple[float, float, float, float] = (0, 0, 1000, 600),
                     gen: int = 1, seq: int = 0) -> dict:
    """The integration adapter: a validated overlay command batch for ``req``.

    Objects are added hidden-by-animation in step order; each step's highlight follows its draw.
    The result is checked by ``jarvis.teach.protocol.validate`` — the overlay's own rules."""
    from ..teach import protocol

    x0, y0, w, h = region
    sx, sy = w / CANVAS_W, h / CANVAS_H
    P = lambda p: [round(x0 + p[0] * sx, 1), round(y0 + p[1] * sy, 1)]  # noqa: E731
    order = {oid: n for n, s in enumerate(req.steps) for oid in s.show}
    cmds: list[dict] = [{"op": "scene.create", "scene": lesson, "monitor": "primary", "hold_s": req.hold_s}]
    delay = 0.0
    for n, step in enumerate(req.steps):
        for oid in step.show:
            o = req.object(oid)
            if o is None:
                continue
            style = {"color": o.style if o.style in _OVERLAY_COLOR else "primary"}
            if o.dashed:
                style["dash"] = "dashed"
            base = {"id": o.id, "style": style, "anim": {"kind": "draw" if o.kind in ("line", "arrow") else "fade",
                                                          "ms": min(step.ms, 2000), "delay": min(delay, 29000)}}
            if o.kind in ("line", "arrow"):
                ob = {**base, "type": o.kind, "from": P(o.points[0]), "to": P(o.points[1])}
            elif o.kind == "text":
                ob = {**base, "type": "text", "x": P(o.points[0])[0], "y": P(o.points[0])[1], "text": o.text[:200]}
            elif o.kind == "rect":
                ob = {**base, "type": "rect", "x": P(o.points[0])[0], "y": P(o.points[0])[1],
                      "w": max(1.0, o.size[0] * sx), "h": max(1.0, o.size[1] * sy)}
            elif o.kind == "circle":
                ob = {**base, "type": "circle", "cx": P(o.points[0])[0], "cy": P(o.points[0])[1],
                      "r": max(1.0, o.size[0] * min(sx, sy))}
            elif o.kind == "node":
                ob = {**base, "type": "node", "x": P(o.points[0])[0], "y": P(o.points[0])[1],
                      "w": max(1.0, o.size[0] * sx), "h": max(1.0, o.size[1] * sy), "label": o.text[:200] or o.id}
            elif o.kind == "edge":
                ob = {**base, "type": "edge", "from": o.ends[0], "to": o.ends[1]}
            else:
                continue
            cmds.append({"op": "shape.add", "object": ob})
        for hid in step.highlight:
            cmds.append({"op": "highlight.show", "target": hid, "color": "attention", "pulse": True})
        delay += step.ms
    batch = {"v": 1, "lesson": lesson, "gen": gen, "seq": seq, "cmds": cmds}
    protocol.validate(batch)
    return batch
