"""The only things that can be done to a Blender scene, and the limits on each.

A ``ScenePlan`` is a list of ``{"op": name, ...args}`` dictionaries. Nothing else crosses into
Blender: no Python source, no operator names, no file paths outside the project. The planner,
the editor and a model's suggestions all end up here, and here is where they are refused.

The same module is imported by the restricted server *inside* Blender, which validates every
batch again before touching ``bpy``. Standard library only, deliberately.

Units: every length in a plan is in **metres** (Blender's internal unit). The scene compiler
converts from the model's millimetres.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1

# ----------------------------------------------------------------------------- limits
MAX_OPS = 2000
MAX_FACES = 400_000                 # estimated faces across the whole plan
MAX_MESH_VERTS = 150_000            # one create_mesh
MAX_CURVE_POINTS = 20_000           # all polygons of one create_curve
MAX_PROFILE_POINTS = 512            # revolve profile
MAX_SEGMENTS = 256                  # cylinder/revolve/sphere segments
MAX_ARRAY = 64
MAX_SUBDIV = 3
MAX_TEXTURE_PX = 2048
MAX_COORD_M = 100.0                 # nothing further than 100 m from the origin
MIN_SIZE_M = 1e-5                   # 0.01 mm
MAX_SIZE_M = 50.0
MAX_BEVEL_SEGMENTS = 12
MIN_REMESH_VOXEL_M = 0.0005
MAX_NAME = 63

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-.()#]{0,62}$")
AXES = ("x", "y", "z")


class PlanError(ValueError):
    """A plan was refused. ``reason`` is safe to log: it never quotes free text from a plan."""

    def __init__(self, reason: str, index: int = -1, op: str = "") -> None:
        self.reason = reason
        self.index = index
        self.op = op
        where = f" (operation {index}: {op})" if index >= 0 else ""
        super().__init__(f"{reason}{where}")


# ----------------------------------------------------------------------------- value checks
def _num(v: Any, what: str, lo: float = -math.inf, hi: float = math.inf) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PlanError(f"{what} must be a number")
    v = float(v)
    if not math.isfinite(v):
        raise PlanError(f"{what} must be finite")
    if v < lo or v > hi:
        raise PlanError(f"{what} out of range")
    return v


def _int(v: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise PlanError(f"{what} must be an integer")
    if v < lo or v > hi:
        raise PlanError(f"{what} out of range")
    return v


def _vec(v: Any, what: str, n: int = 3, lo: float = -MAX_COORD_M, hi: float = MAX_COORD_M) -> list:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise PlanError(f"{what} must be {n} numbers")
    return [_num(x, what, lo, hi) for x in v]


def _size(v: Any, what: str) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) <= 0:
        raise PlanError(f"{what} must be positive")
    return _num(v, what, MIN_SIZE_M, MAX_SIZE_M)


def _name(v: Any, what: str = "name") -> str:
    if not isinstance(v, str) or not NAME_RE.match(v):
        raise PlanError(f"{what} is not a valid object name")
    return v


def _choice(v: Any, what: str, options: Iterable[str]) -> str:
    options = tuple(options)
    if v not in options:
        raise PlanError(f"{what} must be one of {', '.join(options)}")
    return v


def _bool(v: Any, what: str) -> bool:
    if not isinstance(v, bool):
        raise PlanError(f"{what} must be true or false")
    return v


def _color(v: Any, what: str = "color") -> list:
    return _vec(v, what, 3, 0.0, 1.0)


def _polygon(v: Any, what: str) -> list:
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        raise PlanError(f"{what} needs at least 3 points")
    return [_vec(p, what, 2) for p in v]


# ----------------------------------------------------------------------------- op specs
@dataclass
class OpSpec:
    required: dict
    optional: dict = field(default_factory=dict)
    creates: bool = False           # the op creates the object named by "name"
    targets: tuple = ()             # arg names that must name an existing object


def _parts(value, check):
    return check(value)


SHAPES = ("box", "cylinder", "sphere", "cone", "torus", "plane")
BOOL_OPS = ("difference", "union", "intersect")
LIGHTS = ("area", "sun", "point", "spot")
VIEWS = ("front", "side", "back", "top", "perspective", "unknown")

OPS: dict[str, OpSpec] = {
    "create_collection": OpSpec({"name": _name}, {"parent": _name}, creates=False),
    "create_primitive": OpSpec(
        {"name": _name, "shape": lambda v: _choice(v, "shape", SHAPES),
         "size": lambda v: [_size(x, "size") for x in _vec(v, "size", 3, -MAX_SIZE_M, MAX_SIZE_M)]},
        {"location": lambda v: _vec(v, "location"), "rotation": lambda v: _vec(v, "rotation", 3, -7.0, 7.0),
         "segments": lambda v: _int(v, "segments", 3, MAX_SEGMENTS), "collection": _name,
         "top_group": lambda v: _bool(v, "top_group")},
        creates=True),
    "create_curve": OpSpec(
        {"name": _name, "polygons": None},
        {"location": lambda v: _vec(v, "location"), "rotation": lambda v: _vec(v, "rotation", 3, -7.0, 7.0),
         "collection": _name, "smooth": lambda v: _bool(v, "smooth")},
        creates=True),
    "create_revolve": OpSpec(
        {"name": _name, "profile": None},
        {"segments": lambda v: _int(v, "segments", 8, MAX_SEGMENTS), "location": lambda v: _vec(v, "location"),
         "collection": _name},
        creates=True),
    "create_mesh": OpSpec(
        {"name": _name, "vertices": None, "faces": None},
        {"location": lambda v: _vec(v, "location"), "collection": _name},
        creates=True),
    "extrude_profile": OpSpec({"target": _name, "depth": lambda v: _size(v, "depth")},
                              {"offset": lambda v: _num(v, "offset", -MAX_SIZE_M, MAX_SIZE_M)},
                              targets=("target",)),
    "bevel": OpSpec({"target": _name, "width": lambda v: _num(v, "width", 0.0, 1.0)},
                    {"segments": lambda v: _int(v, "segments", 1, MAX_BEVEL_SEGMENTS),
                     "only_group": lambda v: _choice(v, "only_group", ("top",)),
                     "name": _name},
                    targets=("target",)),
    "boolean": OpSpec({"target": _name, "cutter": _name, "operation": lambda v: _choice(v, "operation", BOOL_OPS)},
                      {"name": _name, "hide_cutter": lambda v: _bool(v, "hide_cutter")},
                      targets=("target", "cutter")),
    "mirror": OpSpec({"target": _name, "axis": lambda v: _choice(v, "axis", AXES)},
                     {"mirror_object": _name, "name": _name}, targets=("target",)),
    "array": OpSpec({"target": _name, "count": lambda v: _int(v, "count", 1, MAX_ARRAY),
                     "offset": lambda v: _vec(v, "offset", 3, -MAX_SIZE_M, MAX_SIZE_M)},
                    {"name": _name}, targets=("target",)),
    "subdivision": OpSpec({"target": _name, "levels": lambda v: _int(v, "levels", 0, MAX_SUBDIV)},
                          {"name": _name}, targets=("target",)),
    "solidify": OpSpec({"target": _name, "thickness": lambda v: _num(v, "thickness", -1.0, 1.0)},
                       {"name": _name}, targets=("target",)),
    "remesh": OpSpec({"target": _name, "voxel_size": lambda v: _num(v, "voxel_size", MIN_REMESH_VOXEL_M, 1.0)},
                     {"checkpoint_version": lambda v: _int(v, "checkpoint_version", 1, 100000),
                      "name": _name}, targets=("target",)),
    "set_transform": OpSpec({"target": _name},
                            {"location": lambda v: _vec(v, "location"),
                             "rotation": lambda v: _vec(v, "rotation", 3, -7.0, 7.0),
                             "scale": lambda v: _vec(v, "scale", 3, 1e-4, 1e4)}, targets=("target",)),
    "set_dimensions": OpSpec({"target": _name,
                              "dimensions": lambda v: [_size(x, "dimension") for x in _vec(v, "dimensions", 3, -MAX_SIZE_M, MAX_SIZE_M)]},
                             {}, targets=("target",)),
    "assign_material": OpSpec({"target": _name, "material": _name},
                              {"color": _color, "metallic": lambda v: _num(v, "metallic", 0, 1),
                               "roughness": lambda v: _num(v, "roughness", 0, 1)}, targets=("target",)),
    "set_parent": OpSpec({"child": _name, "parent": _name}, {}, targets=("child", "parent")),
    "set_properties": OpSpec({"target": _name, "props": None}, {}, targets=("target",)),
    "set_visibility": OpSpec({"target": _name, "visible": lambda v: _bool(v, "visible")}, {}, targets=("target",)),
    "add_camera": OpSpec({"name": _name, "location": lambda v: _vec(v, "location"),
                          "look_at": lambda v: _vec(v, "look_at")},
                         {"kind": lambda v: _choice(v, "kind", ("orthographic", "perspective")),
                          "ortho_scale": lambda v: _num(v, "ortho_scale", 1e-4, 200.0),
                          "lens": lambda v: _num(v, "lens", 4.0, 500.0), "collection": _name,
                          "view": lambda v: _choice(v, "view", VIEWS)},
                         creates=True),
    "add_light": OpSpec({"name": _name, "kind": lambda v: _choice(v, "kind", LIGHTS),
                         "location": lambda v: _vec(v, "location")},
                        {"energy": lambda v: _num(v, "energy", 0.0, 100000.0), "collection": _name,
                         "look_at": lambda v: _vec(v, "look_at"), "size": lambda v: _num(v, "size", 0.0, 50.0)},
                        creates=True),
    "add_reference_plane": OpSpec({"name": _name, "image": None, "view": lambda v: _choice(v, "view", VIEWS),
                                   "size": lambda v: _vec(v, "size", 2, MIN_SIZE_M, MAX_SIZE_M)},
                                  {"location": lambda v: _vec(v, "location"), "collection": _name,
                                   "opacity": lambda v: _num(v, "opacity", 0.0, 1.0)},
                                  creates=True),
    "delete_object": OpSpec({"target": _name}, {}, targets=("target",)),
}


def _check_polygons(v) -> int:
    if not isinstance(v, (list, tuple)) or not v:
        raise PlanError("polygons must be a non-empty list")
    total = 0
    for poly in v:
        if not isinstance(poly, dict) or set(poly) - {"outer", "holes"}:
            raise PlanError("each polygon is {outer, holes}")
        outer = _polygon(poly.get("outer"), "polygon outline")
        total += len(outer)
        for hole in poly.get("holes") or []:
            total += len(_polygon(hole, "polygon hole"))
    if total > MAX_CURVE_POINTS:
        raise PlanError("curve has too many points")
    return total


def _check_profile(v) -> int:
    if not isinstance(v, (list, tuple)) or not (2 <= len(v) <= MAX_PROFILE_POINTS):
        raise PlanError("profile must have 2..512 points")
    for p in v:
        r, z = _vec(p, "profile point", 2)
        if r < 0:
            raise PlanError("profile radius must not be negative")
    return len(v)


def _check_mesh(verts, faces) -> tuple[int, int]:
    if not isinstance(verts, (list, tuple)) or not isinstance(faces, (list, tuple)):
        raise PlanError("mesh needs vertices and faces lists")
    if len(verts) > MAX_MESH_VERTS or len(faces) > MAX_MESH_VERTS * 2:
        raise PlanError("mesh exceeds the polygon limit")
    for v in verts:
        _vec(v, "vertex")
    n = len(verts)
    for f in faces:
        if not isinstance(f, (list, tuple)) or not (3 <= len(f) <= 64):
            raise PlanError("face must have 3..64 vertex indices")
        for i in f:
            _int(i, "face index", 0, max(0, n - 1))
    return n, len(faces)


def _check_props(v) -> None:
    if not isinstance(v, dict) or len(v) > 64:
        raise PlanError("props must be a small mapping")
    for k, val in v.items():
        if not isinstance(k, str) or not re.fullmatch(r"jarvis_[a-z_]{1,40}", k):
            raise PlanError("property names must start with jarvis_")
        if isinstance(val, str):
            if len(val) > 4000:
                raise PlanError("property value too long")
        elif isinstance(val, bool) or isinstance(val, (int, float)):
            if isinstance(val, float) and not math.isfinite(val):
                raise PlanError("property must be finite")
        else:
            raise PlanError("property values must be text or numbers")


# ----------------------------------------------------------------------------- paths
def safe_path(path: str, roots: Iterable[str], *, must_exist: bool = False) -> str:
    """Resolve ``path`` and require it to be inside one of ``roots`` after following symlinks.

    Refuses ``..`` escapes, absolute paths elsewhere, and symlinks that point out of the root —
    both a symlinked file and a symlinked directory on the way.
    """
    if not isinstance(path, str) or not path or "\x00" in path:
        raise PlanError("path is not valid")
    real = os.path.realpath(path)
    for root in roots:
        rroot = os.path.realpath(root)
        if real == rroot or real.startswith(rroot.rstrip(os.sep) + os.sep):
            if must_exist and not os.path.exists(real):
                raise PlanError("file does not exist")
            return real
    raise PlanError("path is outside the approved project folders")


# ----------------------------------------------------------------------------- the check
@dataclass
class PlanReport:
    ops: int
    faces: int
    creates: list


def _faces_of(op: dict) -> int:
    kind = op["op"]
    if kind == "create_primitive":
        seg = op.get("segments", 32)
        return {"box": 6, "plane": 1, "cylinder": seg + 2, "cone": seg + 1,
                "sphere": seg * seg // 2, "torus": seg * 12}[op["shape"]]
    if kind == "create_curve":
        return _check_polygons(op["polygons"]) * 4
    if kind == "create_revolve":
        return len(op["profile"]) * op.get("segments", 64)
    if kind == "create_mesh":
        return len(op["faces"])
    if kind == "add_reference_plane":
        return 1
    return 0


def validate_plan(plan: Any, *, known: Iterable[str] = (), image_roots: Iterable[str] = ()) -> PlanReport:
    """Check a whole plan before anything runs. Raises ``PlanError``; returns a small report.

    ``known`` are objects already in the scene; every target must be known or created earlier in
    the same plan. Unknown operations and unknown arguments are refused, not ignored — an extra
    ``"code": "..."`` field is a sign the plan did not come from us.
    """
    if not isinstance(plan, (list, tuple)):
        raise PlanError("a plan is a list of operations")
    if len(plan) > MAX_OPS:
        raise PlanError("too many operations")
    names = set(known)
    faces: dict[str, int] = {}
    total = 0
    created: list[str] = []
    for i, op in enumerate(plan):
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            raise PlanError("each operation is an object with an 'op'", i)
        kind = op["op"]
        spec = OPS.get(kind)
        if spec is None:
            raise PlanError("unknown operation", i, kind[:40])
        args = {k: v for k, v in op.items() if k != "op"}
        extra = set(args) - set(spec.required) - set(spec.optional)
        if extra:
            raise PlanError("unexpected argument", i, kind)
        try:
            for key, check in spec.required.items():
                if key not in args:
                    raise PlanError(f"missing {key}")
                if check is not None:
                    check(args[key])
            for key, check in spec.optional.items():
                if key in args and check is not None:
                    check(args[key])
            if kind == "create_curve":
                _check_polygons(args["polygons"])
            elif kind == "create_revolve":
                _check_profile(args["profile"])
            elif kind == "create_mesh":
                _check_mesh(args["vertices"], args["faces"])
            elif kind == "set_properties":
                _check_props(args["props"])
            elif kind == "add_reference_plane":
                if not isinstance(args["image"], str):
                    raise PlanError("image must be a path")
                safe_path(args["image"], image_roots, must_exist=False)
            elif kind == "remesh" and "checkpoint_version" not in args:
                raise PlanError("remesh is destructive and needs a checkpoint first")
        except PlanError as exc:
            raise PlanError(exc.reason, i, kind) from None
        for key in spec.targets:
            if args[key] not in names:
                raise PlanError("target does not exist", i, kind)
        if kind == "mirror" and "mirror_object" in args and args["mirror_object"] not in names:
            raise PlanError("mirror pivot does not exist", i, kind)
        if kind == "delete_object":
            names.discard(args["target"])
        if spec.creates or kind == "create_collection":
            names.add(args["name"])
            created.append(args["name"])
        cost = _faces_of(op)
        if spec.creates:
            faces[args["name"]] = cost
        elif kind == "array":
            faces[args["target"]] = faces.get(args["target"], 50) * args["count"]
        elif kind == "mirror":
            faces[args["target"]] = faces.get(args["target"], 50) * 2
        elif kind == "subdivision":
            faces[args["target"]] = faces.get(args["target"], 50) * (4 ** args["levels"])
        total = sum(faces.values())
        if total > MAX_FACES:
            raise PlanError("plan exceeds the polygon budget", i, kind)
    return PlanReport(ops=len(plan), faces=total, creates=created)
