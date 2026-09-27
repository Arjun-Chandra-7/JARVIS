"""The shapes every part of 3D Studio agrees on.

Plain dataclasses with ``to_dict``/``from_dict``, standard library only: the same definitions are
read by the JARVIS process and — for the few the Blender side needs — by the restricted server
running inside Blender, which must not import anything heavier.

Lengths inside JARVIS are millimetres. The scene compiler converts to Blender metres at the
boundary, once.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Optional


class Evidence(str, Enum):
    """How much a piece of geometry is supported by what the user showed or said.

    Ordered from strongest to weakest; ``weakest`` of several is the honest label for the whole.
    """
    VERIFIED = "verified"          # directly supported by a measurement or by several views
    CONSTRAINED = "constrained"    # inferred inside supplied dimensions or a stated symmetry
    ESTIMATED = "estimated"        # inferred from visual evidence alone
    INVENTED = "invented"          # made up because there was no evidence at all

    @staticmethod
    def weakest(items) -> "Evidence":
        order = [Evidence.VERIFIED, Evidence.CONSTRAINED, Evidence.ESTIMATED, Evidence.INVENTED]
        worst = 0
        for item in items:
            worst = max(worst, order.index(Evidence(item)))
        return order[worst]


class Fidelity(str, Enum):
    """What kind of result the whole reconstruction is. Said to the user in these words."""
    DIMENSIONAL = "dimensionally accurate reconstruction"
    VISUAL = "visually matched reconstruction"
    ARTISTIC = "artistic interpretation"


class Mode(str, Enum):
    VECTOR = "vector"              # logos, icons, line art, silhouettes
    PARAMETRIC = "parametric"      # products dominated by primitives
    DIMENSIONED = "dimensioned"    # orthographic / dimensioned drawings
    MULTIVIEW = "multiview"        # several photos, turntable frames, orbit video
    ORGANIC = "organic"            # characters, sculpture, irregular forms
    ARTISTIC = "artistic"          # "invent the unseen back"


class ViewKind(str, Enum):
    FRONT = "front"
    SIDE = "side"
    BACK = "back"
    TOP = "top"
    PERSPECTIVE = "perspective"
    UNKNOWN = "unknown"


class JobState(str, Enum):
    QUEUED = "queued"
    CAPTURING = "capturing"
    ANALYSING = "analysing"
    NEEDS_INPUT = "needs_input"
    BUILDING = "building"
    REFINING = "refining"
    MATERIALS = "materials"
    COMPARING = "comparing"
    READY = "ready"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in (JobState.READY, JobState.CANCELLED, JobState.FAILED)


# What the overlay shows for each state — the compact status line from the spec.
STATE_LABEL = {
    JobState.QUEUED: "Queued",
    JobState.CAPTURING: "Capturing reference",
    JobState.ANALYSING: "Analysing reference",
    JobState.NEEDS_INPUT: "Needs another view",
    JobState.BUILDING: "Building parts",
    JobState.REFINING: "Refining geometry",
    JobState.MATERIALS: "Applying materials",
    JobState.COMPARING: "Comparing with reference",
    JobState.READY: "Ready in Blender",
    JobState.PAUSED: "Paused",
    JobState.CANCELLED: "Cancelled",
    JobState.FAILED: "Failed safely",
}


class ErrorCategory(str, Enum):
    """Safe, content-free reasons a job stopped. Never the exception text of a capture."""
    NONE = ""
    BLENDER_UNAVAILABLE = "blender_unavailable"
    BLENDER_CRASHED = "blender_crashed"
    BRIDGE_LOST = "bridge_disconnected"
    INVALID_PLAN = "invalid_scene_operation"
    RESOURCE_LIMIT = "resource_limit"
    SAVE_FAILED = "save_failed"
    SENSITIVE_CAPTURE = "sensitive_capture_refused"
    CAPTURE_FAILED = "capture_failed"
    MISSING_EVIDENCE = "missing_evidence"
    PROVIDER_REFUSED = "provider_not_approved"
    CANCELLED = "cancelled"
    INTERNAL = "internal_error"


class Retention(str, Enum):
    TRANSIENT = "transient"        # deleted as soon as it has been cropped/used
    PROJECT = "project"            # kept beside the project until "delete the references" or TTL
    KEPT = "kept"                  # the user asked to keep it


def new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%m%d')}-{secrets.token_hex(3)}"


class _Record:
    """to_dict/from_dict for nested dataclasses and enums, without a third-party library."""

    def to_dict(self) -> dict:
        def conv(v):
            if isinstance(v, Enum):
                return v.value
            if isinstance(v, _Record):
                return v.to_dict()
            if isinstance(v, (list, tuple)):
                return [conv(x) for x in v]
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            return v
        return {f.name: conv(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, raw: dict):
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for key, value in (raw or {}).items():
            if key not in known:
                continue
            kwargs[key] = _coerce(cls, key, value)
        return cls(**kwargs)


def _coerce(cls, key, value):
    nested = _NESTED.get((cls.__name__, key))
    if nested is None:
        return value
    kind, target = nested
    if value is None:
        return None
    if kind == "enum":
        return target(value)
    if kind == "record":
        return target.from_dict(value)
    if kind == "records":
        return [target.from_dict(v) for v in value]
    if kind == "enums":
        return [target(v) for v in value]
    return value


@dataclass
class Crop(_Record):
    x: int
    y: int
    width: int
    height: int


@dataclass
class ReferenceAsset(_Record):
    id: str
    source: str                            # region, window, browser_image, clipboard, file, video_frame, camera, drawing
    path: str = ""                         # temporary local path of the *cropped* image ("" once deleted)
    width: int = 0
    height: int = 0
    crop: Optional[Crop] = None
    perspective: str = "unknown"           # orthographic | perspective | unknown
    privacy: str = "unchecked"             # clear | refused | unchecked
    captured_at: float = field(default_factory=time.time)
    retention: Retention = Retention.PROJECT
    view: ViewKind = ViewKind.UNKNOWN      # what the user said this image shows
    deleted: bool = False

    def safe(self) -> dict:
        """What may be logged: no path, no pixels, no text."""
        return {"id": self.id, "source": self.source, "w": self.width, "h": self.height,
                "view": self.view.value, "privacy": self.privacy, "retention": self.retention.value,
                "deleted": self.deleted}


@dataclass
class CameraEstimate(_Record):
    kind: str = "orthographic"             # orthographic | perspective
    view: ViewKind = ViewKind.FRONT
    elevation_deg: float = 0.0
    azimuth_deg: float = 0.0
    focal_mm: float = 50.0
    sensor_mm: float = 36.0
    distance_mm: float = 0.0
    ortho_scale_mm: float = 0.0            # width of the orthographic frame, in mm
    confidence: float = 0.0
    evidence: Evidence = Evidence.ESTIMATED


@dataclass
class DetectedDimension(_Record):
    value_mm: float
    raw_unit: str                          # "mm", "cm", "in", "" when ambiguous
    axis: str                              # horizontal | vertical
    span_px: float = 0.0                   # length of the dimension it labels, when paired
    confidence: float = 0.0


@dataclass
class EvidenceView(_Record):
    reference_id: str
    view: ViewKind = ViewKind.UNKNOWN
    camera: Optional[CameraEstimate] = None
    scale_mm_per_px: float = 0.0
    scale_confidence: float = 0.0
    landmarks: list = field(default_factory=list)         # [[name, x, y], ...] in reference pixels
    mask_path: str = ""                                   # segmentation mask (temporary)
    silhouette_px: float = 0.0                            # silhouette area in pixels
    dimensions: list = field(default_factory=list)        # [DetectedDimension]
    confidence: float = 0.0
    notes: list = field(default_factory=list)


@dataclass
class ModelPart(_Record):
    name: str                              # stable, human-readable: "Base", "Post", "Hole 1"
    geometry: str                          # box | cylinder | sphere | curve_extrude | revolve | mesh | cutter
    params: dict = field(default_factory=dict)            # editable parameters, mm
    parent: str = ""
    children: list = field(default_factory=list)
    location: list = field(default_factory=lambda: [0.0, 0.0, 0.0])   # mm
    rotation: list = field(default_factory=lambda: [0.0, 0.0, 0.0])   # degrees
    material: str = ""
    modifiers: list = field(default_factory=list)         # [{"type": "bevel", ...}] non-destructive
    locked: list = field(default_factory=list)            # ["all"] or ["geometry", "material", ...]
    evidence: Evidence = Evidence.ESTIMATED
    confidence: float = 0.5
    aliases: list = field(default_factory=list)           # words that name it: "shell", "tom"
    role: str = "part"                                    # part | cutter | reference

    @property
    def is_locked(self) -> bool:
        return bool(self.locked)

    def curve_extent(self) -> tuple:
        """(xmin, xmax, ymin, ymax) of this layer's own outlines, in its profile plane (mm)."""
        pts = [pt for poly in self.params.get("polygons", []) for pt in poly["outer"]]
        if not pts:
            return 0.0, 0.0, 0.0, 0.0
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        return min(xs), max(xs), min(ys), max(ys)

    def dimensions(self) -> list:
        p = self.params
        if self.geometry == "box":
            return [p["x"], p["y"], p["z"]]
        if self.geometry in ("cylinder", "cutter"):
            return [2 * p["radius"], 2 * p["radius"], p["height"]]
        if self.geometry == "sphere":
            return [2 * p["radius"]] * 3
        if self.geometry == "revolve":
            r = max(pt[0] for pt in p["profile"])
            zs = [pt[1] for pt in p["profile"]]
            return [2 * r, 2 * r, max(zs) - min(zs)]
        if self.geometry == "curve_extrude":
            x0, x1, y0, y1 = self.curve_extent()
            return [x1 - x0, y1 - y0, p["depth"]]
        return list(p.get("bounds", [0.0, 0.0, 0.0]))


@dataclass
class Material(_Record):
    name: str
    color: list = field(default_factory=lambda: [0.8, 0.8, 0.8])  # linear-ish RGB 0..1
    metallic: float = 0.0
    roughness: float = 0.5
    evidence: Evidence = Evidence.ESTIMATED


@dataclass
class StudioModel(_Record):
    """The editable, parametric description of the object. The source of the Blender scene."""
    name: str
    parts: list = field(default_factory=list)             # [ModelPart]
    materials: list = field(default_factory=list)         # [Material]
    cameras: list = field(default_factory=list)           # [CameraEstimate] reference cameras
    units: str = "mm"
    symmetry: list = field(default_factory=list)          # ["x"] mirror planes found
    repeats: list = field(default_factory=list)           # [{"parts": [...], "count": n, "spacing": mm}]
    fidelity: Fidelity = Fidelity.VISUAL
    category: str = "object"

    def part(self, name: str) -> Optional[ModelPart]:
        for p in self.parts:
            if p.name == name:
                return p
        return None

    def evidence_summary(self) -> dict:
        out: dict[str, list] = {}
        for p in self.parts:
            if p.role == "part" or p.role == "cutter":
                out.setdefault(Evidence(p.evidence).value, []).append(p.name)
        return out


@dataclass
class EditOperation(_Record):
    target: list                           # part names
    operation: str                         # scale, set_dimension, move, add_holes, material, bevel, lock, ...
    amount: float = 0.0
    relative: bool = True
    unit: str = ""
    axis: str = ""
    constraints: list = field(default_factory=list)
    locked_areas: list = field(default_factory=list)
    expected: str = ""                     # expected visual effect, in words
    undo_version: int = 0                  # the version to restore to undo this
    extra: dict = field(default_factory=dict)


@dataclass
class ValidationResult(_Record):
    reference_view: str
    silhouette_iou: float = 0.0
    contour_distance_px: float = 0.0
    landmark_error_px: float = 0.0
    edge_mismatch: float = 0.0
    proportion_error: float = 0.0
    dimension_error_mm: float = 0.0
    color_delta_e: float = 0.0
    confidence: float = 0.0
    uncertainty: list = field(default_factory=list)
    plateau_reason: str = ""
    iteration: int = 0


@dataclass
class ReconstructionJob(_Record):
    id: str
    request: str                           # what the user asked, scrubbed; never OCR text
    references: list = field(default_factory=list)        # [ReferenceAsset]
    mode: Optional[Mode] = None
    state: JobState = JobState.QUEUED
    stage_note: str = ""
    requested_accuracy: str = "visual"    # visual | exact
    missing_evidence: list = field(default_factory=list)
    question: str = ""                     # the one question asked when evidence is missing
    project_path: str = ""
    version: int = 0
    validation: list = field(default_factory=list)        # [ValidationResult] (before/after)
    exports: list = field(default_factory=list)           # [{"format", "path", "ok", "checks"}]
    error: ErrorCategory = ErrorCategory.NONE
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    stages_done: list = field(default_factory=list)
    known_dimensions: dict = field(default_factory=dict)  # {"width": mm, ...} said by the user
    purpose: str = ""                      # render | game | print
    fidelity: str = ""
    evidence: dict = field(default_factory=dict)


_NESTED = {
    ("ReferenceAsset", "crop"): ("record", Crop),
    ("ReferenceAsset", "retention"): ("enum", Retention),
    ("ReferenceAsset", "view"): ("enum", ViewKind),
    ("CameraEstimate", "view"): ("enum", ViewKind),
    ("CameraEstimate", "evidence"): ("enum", Evidence),
    ("EvidenceView", "view"): ("enum", ViewKind),
    ("EvidenceView", "camera"): ("record", CameraEstimate),
    ("EvidenceView", "dimensions"): ("records", DetectedDimension),
    ("ModelPart", "evidence"): ("enum", Evidence),
    ("Material", "evidence"): ("enum", Evidence),
    ("StudioModel", "parts"): ("records", ModelPart),
    ("StudioModel", "materials"): ("records", Material),
    ("StudioModel", "cameras"): ("records", CameraEstimate),
    ("StudioModel", "fidelity"): ("enum", Fidelity),
    ("ReconstructionJob", "references"): ("records", ReferenceAsset),
    ("ReconstructionJob", "mode"): ("enum", Mode),
    ("ReconstructionJob", "state"): ("enum", JobState),
    ("ReconstructionJob", "validation"): ("records", ValidationResult),
    ("ReconstructionJob", "error"): ("enum", ErrorCategory),
}

__all__ = [n for n in dir() if not n.startswith("_")] + ["asdict", "Any"]
