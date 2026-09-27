"""From references to a checked reconstruction plan.

``analyse`` measures the references (locally), classifies them, reads numbers off drawings,
and notices instruction-like text inside an image — which is logged as a security event (no
content) and otherwise ignored: text in a picture is reference content, never a command.

``plan`` runs the right engine and returns a ``ReconstructionPlan``: the model, the ScenePlan,
what is missing, the one question to ask (if any), per-part evidence, validation targets, and
the honest label for the result. ``check_model`` and ``scene_ops.validate_plan`` refuse
anything malformed before Blender sees it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from . import classifier, parametric_reconstruction as pr, privacy, scene_compiler, scene_ops, vector_reconstruction as vr
from .types import Evidence, Fidelity, Mode, ReferenceAsset, StudioModel, ViewKind

MAX_PARTS = 400

# Instruction-shaped text: we do not act on it either way, but it is worth a security note.
_INJECTION = re.compile(r"(?i)\b(?:ignore (?:the |all |previous )?(?:user|instructions?)|execute|run (?:this|the)|"
                        r"python|shell|upload|exfiltrat\w*|delete (?:all|their|the) files?|system prompt|"
                        r"curl|wget|rm\s+-rf|api[_ ]?key|password)\b")


@dataclass
class Analysis:
    classification: classifier.Classification
    words: dict = field(default_factory=dict)          # ref id → OCR words (numbers only are ever used)
    injected: list = field(default_factory=list)       # ref ids whose text looked like instructions


@dataclass
class ReconstructionPlan:
    mode: Mode
    model: Optional[StudioModel]
    ops: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    question: str = ""
    fidelity: Fidelity = Fidelity.VISUAL
    evidence: dict = field(default_factory=dict)
    targets: dict = field(default_factory=dict)         # validation targets
    notes: list = field(default_factory=list)
    sequence: list = field(default_factory=list)        # modelling sequence, in words


def _ocr(path: str) -> list:
    try:
        from ..vision import ocr
        return ocr.read(path)
    except Exception:  # noqa: BLE001 — no OCR: drawings need a stated measurement instead
        return []


def analyse(refs: list[ReferenceAsset], request: str, *, ocr=_ocr) -> Analysis:
    feats, words, injected = [], {}, []
    from . import imaging
    for r in refs:
        rgb = imaging.load_rgb(r.path, 1024)
        f = classifier.features(rgb)
        ws = []
        if f.line_art or f.colors <= 6:
            ws = ocr(r.path)
            text = " ".join(getattr(w, "text", "") for w in ws)
            if _INJECTION.search(text):
                injected.append(r.id)
                privacy.security_event("image_text_ignored", category="instruction-like text in a reference",
                                       refs=1, source=r.source)
            f.has_numbers = any(re.fullmatch(r"[ØøR⌀]?\d{1,5}(?:[.,]\d+)?\s*(?:mm|cm|in|m|\"|″)?",
                                             getattr(w, "text", "").replace(" ", "")) for w in ws)
            del text
        words[r.id] = ws
        feats.append(f)
    cls = classifier.classify(refs, request, feats=feats)
    return Analysis(cls, words, injected)


def check_model(model: StudioModel) -> None:
    """Refuse a model no sane plan should contain (before the ScenePlan's own limits)."""
    if len(model.parts) > MAX_PARTS:
        raise scene_ops.PlanError("too many parts")
    names = set()
    for p in model.parts:
        if not scene_ops.NAME_RE.match(p.name) or p.name in names:
            raise scene_ops.PlanError("part names must be unique and plain")
        names.add(p.name)
        for v in p.location + p.rotation:
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                raise scene_ops.PlanError("invalid transform")
        if p.geometry in ("box", "cylinder", "cutter", "sphere"):
            if any((not math.isfinite(d)) or d <= 0 for d in p.dimensions()):
                raise scene_ops.PlanError("dimensions must be positive")
        for mod in p.modifiers:
            if mod.get("type") == "array" and not (1 <= int(mod.get("count", 0)) <= scene_ops.MAX_ARRAY):
                raise scene_ops.PlanError("array count out of range")


def plan(refs: list[ReferenceAsset], request: str, analysis: Analysis, *, known: Optional[dict] = None,
         unit: Optional[str] = None, name: str = "Model", image_roots: Optional[list] = None,
         allow_estimate: bool = False) -> ReconstructionPlan:
    known = known or {}
    cls = analysis.classification
    mode = cls.mode
    exact = cls.exact_requested and not allow_estimate
    out = ReconstructionPlan(mode=mode, model=None)
    try:
        if mode == Mode.DIMENSIONED:
            by_view = {r.view.value: r for r in refs if r.view in (ViewKind.FRONT, ViewKind.SIDE, ViewKind.TOP)}
            if len(by_view) < 3 and not allow_estimate:
                # A dimensioned drawing is about its dimensions: ask for the views that fix them,
                # rather than extruding one view and calling it a model.
                have = sorted(by_view) or ["one view"]
                need = [v for v in ("front", "side", "top") if v not in by_view]
                raise pr.NeedsInput(f"I have the {' and '.join(have)} drawing. Show me the {' and '.join(need)} "
                                    f"view{'s' if len(need) > 1 else ''} too — or say \"just estimate it\".",
                                    [f"{v} view" for v in need])
            if len(by_view) < 3:
                mode = out.mode = Mode.VECTOR
            else:
                views = {v: r.path for v, r in by_view.items()}
                words = {v: analysis.words.get(r.id, []) for v, r in by_view.items()}
                model, facts = pr.from_drawings(views, words, unit=unit, known=known, name=name, exact=exact)
                out.sequence = ["read the three views", "match regions across views", "build boxes and cylinders",
                                "mirror and array the repeated parts", "parent everything to the base",
                                "validate every view and the labelled dimensions"]
                out.targets = {"labels_mm": facts["labels"], "size_mm": facts["size_mm"]}
                out.notes += facts["unexplained"]
        if mode == Mode.VECTOR:
            if exact and not (known.get("depth") or known.get("thickness")):
                raise pr.NeedsInput("A flat picture can't tell me its real thickness. How thick should it be — or "
                                    "can you show me the side view?", ["depth measurement or side view"])
            opts = vr.VectorOptions(width_mm=known.get("width"), height_mm=known.get("height"),
                                    depth_mm=known.get("depth") or known.get("thickness"))
            model, facts = vr.reconstruct(refs[0].path, name, opts)
            out.sequence = ["remove the background", "split the colour layers", "trace contours with holes",
                            "extrude and bevel each layer", "compare the front silhouette"]
            out.targets = {"silhouette_iou": 0.97}
        elif mode in (Mode.PARAMETRIC, Mode.ORGANIC, Mode.ARTISTIC):
            model, facts = pr.from_single_view(refs[0].path, known=known, name=name, exact=exact)
            if mode == Mode.ORGANIC and facts.get("kind") == "extrude":
                out.notes.append("organic shape: built as a rounded extrusion; a generative base mesh needs an "
                                 "approved provider")
            if mode == Mode.ARTISTIC:
                for p in model.parts:
                    p.evidence = Evidence.INVENTED
                    p.params.setdefault("evidence", {})["back"] = Evidence.INVENTED.value
                model.fidelity = Fidelity.ARTISTIC
            out.sequence = ["segment the silhouette", "test symmetry", "revolve or extrude the outline",
                            "estimate the camera", "refine against the silhouette"]
            out.targets = {"silhouette_iou": 0.95}
        elif mode == Mode.MULTIVIEW:
            from . import multiview
            try:
                model, facts = multiview.reconstruct([r.path for r in refs], height_mm=known.get("height"), name=name)
            except ValueError:
                raise pr.NeedsInput("Those views don't agree with each other — are they all of the same object, "
                                    "turning in one direction? Show me at least three clear angles.",
                                    ["consistent views"]) from None
            out.sequence = ["check the views agree", "carve the visual hull", "surface it", "keep the source cameras"]
            out.targets = {"silhouette_iou": 0.9}
    except pr.NeedsInput as need:
        out.missing, out.question = need.missing, need.question
        return out
    check_model(model)
    ops = scene_compiler.build(model)
    scene_ops.validate_plan(ops, image_roots=image_roots or [])
    out.model, out.ops, out.facts = model, ops, facts
    out.fidelity = model.fidelity
    out.evidence = model.evidence_summary()
    if exact and model.fidelity != Fidelity.DIMENSIONAL:
        out.missing.append("measurements")
    return out


def describe(p: ReconstructionPlan) -> str:
    """The honest one-liner about what kind of result this is."""
    ev = p.evidence
    bits = []
    if ev.get("verified"):
        bits.append(f"verified: {', '.join(ev['verified'])}")
    if ev.get("constrained"):
        bits.append(f"built to the measurements: {', '.join(ev['constrained'])}")
    if ev.get("estimated"):
        bits.append(f"estimated: {', '.join(ev['estimated'])}")
    if ev.get("invented"):
        bits.append(f"partly invented: {', '.join(ev['invented'])}")
    return f"This is a {p.fidelity.value}" + (f" ({'; '.join(bits)})" if bits else "") + "."
