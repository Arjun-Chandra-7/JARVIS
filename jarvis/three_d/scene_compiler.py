"""StudioModel (millimetres, parametric) → ScenePlan (metres, allowlisted operations).

The model is the editable description; the plan is the only thing Blender ever receives. A
full build lays out collections, parts, modifiers, materials, hierarchy, reference cameras and a
neutral studio light rig. ``rebuild_parts`` produces the smaller plan that replaces just the
parts an edit touched (and whatever depends on them), so the rest of the scene is left exactly
as it was.
"""
from __future__ import annotations

import json
import math
from typing import Iterable, Optional

from .types import CameraEstimate, ModelPart, StudioModel

MM = 0.001


def _m(v) -> list:
    return [round(float(x) * MM, 7) for x in v]


def _rad(v) -> list:
    return [round(math.radians(float(x)), 7) for x in v]


def collections(model: StudioModel) -> dict:
    return {"parts": f"{model.name} Parts"[:63], "cutters": f"{model.name} Cutters"[:63],
            "refs": "References", "rig": "Cameras and Lights"}


def _props(part: ModelPart) -> dict:
    slim = {k: v for k, v in part.params.items() if k not in ("polygons", "vertices", "faces", "profile")}
    return {"jarvis_geometry": part.geometry, "jarvis_evidence": str(part.evidence.value),
            "jarvis_confidence": float(part.confidence), "jarvis_locked": ",".join(part.locked),
            "jarvis_params": json.dumps(slim, separators=(",", ":"), default=str)[:3900]}


def part_ops(model: StudioModel, part: ModelPart) -> list[dict]:
    cols = collections(model)
    col = cols["cutters"] if part.role == "cutter" else cols["parts"]
    p = part.params
    loc, rot = _m(part.location), _rad(part.rotation)
    ops: list[dict] = []
    g = part.geometry
    if g == "box":
        ops.append({"op": "create_primitive", "name": part.name, "shape": "box", "size": _m([p["x"], p["y"], p["z"]]),
                    "location": loc, "rotation": rot, "collection": col, "top_group": True})
    elif g in ("cylinder", "cutter"):
        ops.append({"op": "create_primitive", "name": part.name, "shape": "cylinder",
                    "size": _m([2 * p["radius"], 2 * p["radius"], p["height"]]), "segments": int(p.get("segments", 64)),
                    "location": loc, "rotation": rot, "collection": col, "top_group": True})
    elif g == "sphere":
        ops.append({"op": "create_primitive", "name": part.name, "shape": "sphere", "size": _m([2 * p["radius"]] * 3),
                    "segments": 48, "location": loc, "rotation": rot, "collection": col})
    elif g == "curve_extrude":
        polys = [{"outer": [[x * MM, y * MM] for x, y in poly["outer"]],
                  "holes": [[[x * MM, y * MM] for x, y in h] for h in poly["holes"]]} for poly in p["polygons"]]
        bevel = float(p.get("bevel", 0.0))
        depth = float(p["depth"])
        ops.append({"op": "create_curve", "name": part.name, "polygons": polys, "location": loc, "rotation": rot,
                    "collection": col, "smooth": bool(p.get("smooth", False))})
        ops.append({"op": "extrude_profile", "target": part.name, "depth": max(depth - 2 * bevel, 1e-5) * MM})
        if bevel > 0:
            ops.append({"op": "bevel", "target": part.name, "width": bevel * MM, "segments": 3})
    elif g == "revolve":
        ops.append({"op": "create_revolve", "name": part.name, "profile": [[r * MM, z * MM] for r, z in p["profile"]],
                    "segments": int(p.get("segments", 96)), "location": loc, "collection": col})
    elif g == "mesh":
        ops.append({"op": "create_mesh", "name": part.name, "vertices": [_m(v) for v in p["vertices"]],
                    "faces": p["faces"], "location": loc, "collection": col})
    else:
        raise ValueError(f"unknown geometry {g}")
    for mod in part.modifiers:
        t = mod["type"]
        if t == "array":
            ops.append({"op": "array", "target": part.name, "count": int(mod["count"]), "offset": _m(mod["offset"]),
                        "name": "Array"})
        elif t == "mirror":
            op = {"op": "mirror", "target": part.name, "axis": mod["axis"], "name": "Mirror"}
            if mod.get("about"):
                op["mirror_object"] = mod["about"]
            ops.append(op)
        elif t == "bevel" and g != "curve_extrude":
            op = {"op": "bevel", "target": part.name, "width": float(mod["width"]) * MM,
                  "segments": int(mod.get("segments", 3)), "name": "Bevel"}
            if mod.get("only") == "top":
                op["only_group"] = "top"
            ops.append(op)
        elif t == "subdivision":
            ops.append({"op": "subdivision", "target": part.name, "levels": int(mod["levels"]), "name": "Subdivision"})
        elif t == "solidify":
            ops.append({"op": "solidify", "target": part.name, "thickness": float(mod["thickness"]) * MM,
                        "name": "Solidify"})
    if part.material and g != "cutter":
        mat = next((m for m in model.materials if m.name == part.material), None)
        if mat is not None:
            ops.append({"op": "assign_material", "target": part.name, "material": mat.name,
                        "color": [float(c) for c in mat.color], "metallic": float(mat.metallic),
                        "roughness": float(mat.roughness)})
    ops.append({"op": "set_properties", "target": part.name, "props": _props(part)})
    return ops


def _link_ops(model: StudioModel, part: ModelPart) -> list[dict]:
    """Operations that connect a part to others: boolean with its host, parent link."""
    ops = []
    if part.role == "cutter" and part.params.get("host"):
        ops.append({"op": "boolean", "target": part.params["host"], "cutter": part.name,
                    "operation": part.params.get("operation", "difference"), "name": f"Cut {part.name}"[:63],
                    "hide_cutter": True})
    if part.parent:
        ops.append({"op": "set_parent", "child": part.name, "parent": part.parent})
    return ops


def bounds_mm(model: StudioModel) -> tuple[list, list]:
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for part in model.parts:
        if part.role != "part":
            continue
        d = part.dimensions()
        rx = abs(part.rotation[0]) % 180
        if part.geometry == "curve_extrude" and 45 < rx < 135:
            d = [d[0], d[2], d[1]]          # stood up: height is z, thickness is y
        for mod in part.modifiers:
            if mod["type"] == "array":
                d = [d[i] + abs(mod["offset"][i]) * (mod["count"] - 1) for i in range(3)]
        c = list(part.location)
        if part.geometry == "curve_extrude":
            x0, x1, y0, y1 = part.curve_extent()
            ox, oy = (x0 + x1) / 2, (y0 + y1) / 2          # the layer's own centre, off the logo's origin
            c = [c[0] + ox, c[1], c[2] + oy] if 45 < rx < 135 else [c[0] + ox, c[1] + oy, c[2]]
        if part.geometry == "revolve":
            c = [c[0], c[1], c[2] + d[2] / 2]
        for mod in part.modifiers:
            if mod["type"] == "array":
                c = [c[i] + mod["offset"][i] * (mod["count"] - 1) / 2 for i in range(3)]
        for i in range(3):
            lo[i] = min(lo[i], c[i] - d[i] / 2)
            hi[i] = max(hi[i], c[i] + d[i] / 2)
    if not math.isfinite(lo[0]):
        return [0, 0, 0], [0, 0, 0]
    return lo, hi


def camera_ops(model: StudioModel, cams: Iterable[CameraEstimate] = ()) -> list[dict]:
    col = collections(model)["rig"]
    lo, hi = bounds_mm(model)
    c = [(lo[i] + hi[i]) / 2 for i in range(3)]
    size = max(hi[i] - lo[i] for i in range(3)) or 100.0
    dist = size * 4
    frame = size * 1.3
    ops = [
        {"op": "add_camera", "name": "Camera Front", "kind": "orthographic", "ortho_scale": frame * MM,
         "location": _m([c[0], c[1] - dist, c[2]]), "look_at": _m(c), "collection": col, "view": "front"},
        {"op": "add_camera", "name": "Camera Side", "kind": "orthographic", "ortho_scale": frame * MM,
         "location": _m([c[0] + dist, c[1], c[2]]), "look_at": _m(c), "collection": col, "view": "side"},
        {"op": "add_camera", "name": "Camera Top", "kind": "orthographic", "ortho_scale": frame * MM,
         "location": _m([c[0], c[1], c[2] + dist]), "look_at": _m([c[0], c[1] + 1e-3, c[2]]), "collection": col,
         "view": "top"},
        {"op": "add_camera", "name": "Camera Perspective", "kind": "perspective", "lens": 50.0,
         "location": _m([c[0] - dist * 0.55, c[1] - dist * 0.75, c[2] + dist * 0.45]), "look_at": _m(c),
         "collection": col, "view": "perspective"},
    ]
    for i, cam in enumerate(cams):
        if cam.kind != "perspective" or not cam.distance_mm:
            continue
        el, az = math.radians(cam.elevation_deg), math.radians(cam.azimuth_deg)
        d = cam.distance_mm
        pos = [c[0] + d * math.cos(el) * math.sin(az), c[1] - d * math.cos(el) * math.cos(az), c[2] + d * math.sin(el)]
        ops.append({"op": "add_camera", "name": f"Camera Reference {i + 1}", "kind": "perspective",
                    "lens": float(cam.focal_mm), "location": _m(pos), "look_at": _m(c), "collection": col,
                    "view": "perspective"})
    # Neutral studio: a key, a fill and a rim; soft area lights, no colour.
    ops += [
        {"op": "add_light", "name": "Key Light", "kind": "area", "location": _m([c[0] - dist, c[1] - dist, c[2] + dist]),
         "look_at": _m(c), "energy": 800.0 * (size / 100) ** 2, "size": size * 2 * MM, "collection": col},
        {"op": "add_light", "name": "Fill Light", "kind": "area", "location": _m([c[0] + dist, c[1] - dist * 0.6, c[2] + dist * 0.3]),
         "look_at": _m(c), "energy": 300.0 * (size / 100) ** 2, "size": size * 2 * MM, "collection": col},
        {"op": "add_light", "name": "Rim Light", "kind": "area", "location": _m([c[0], c[1] + dist, c[2] + dist * 0.8]),
         "look_at": _m(c), "energy": 400.0 * (size / 100) ** 2, "size": size * MM, "collection": col},
    ]
    return ops


def reference_ops(model: StudioModel, refs: list, image_paths: dict) -> list[dict]:
    """Reference pictures on planes behind the model, in their own collection."""
    lo, hi = bounds_mm(model)
    c = [(lo[i] + hi[i]) / 2 for i in range(3)]
    size = [hi[i] - lo[i] for i in range(3)]
    ops = []
    for i, ref in enumerate(refs):
        path = image_paths.get(ref.id)
        if not path:
            continue
        view = ref.view.value if ref.view.value in ("front", "side", "top", "back") else "front"
        aspect = (ref.width / ref.height) if ref.width and ref.height else 1.0
        h = max(size[2] if view != "top" else size[1], 10.0) * 1.1
        w = h * aspect
        offset = {"front": [c[0], hi[1] + max(size) * 0.8 + i, c[2]], "back": [c[0], lo[1] - max(size) * 0.8, c[2]],
                  "side": [lo[0] - max(size) * 0.8, c[1], c[2]], "top": [c[0], c[1], lo[2] - max(size) * 0.3]}[view]
        ops.append({"op": "add_reference_plane", "name": f"Reference {i + 1} ({view})", "image": path, "view": view,
                    "size": _m([w, h]), "location": _m(offset), "collection": collections(model)["refs"],
                    "opacity": 0.6})
    return ops


def _build_order(p: ModelPart) -> tuple:
    """Pivots and parents before the parts that refer to them; cutters last."""
    refers = any(m.get("about") for m in p.modifiers)
    return (p.role == "cutter", refers, p.parent != "")


def build(model: StudioModel, *, refs: Optional[list] = None, image_paths: Optional[dict] = None,
          cameras: bool = True) -> list[dict]:
    cols = collections(model)
    ops: list[dict] = [{"op": "create_collection", "name": cols["parts"]},
                       {"op": "create_collection", "name": cols["cutters"]},
                       {"op": "create_collection", "name": cols["rig"]}]
    if refs and image_paths:
        ops.append({"op": "create_collection", "name": cols["refs"]})
    ordered = sorted(model.parts, key=_build_order)
    for part in ordered:
        ops += part_ops(model, part)
    for part in ordered:
        ops += _link_ops(model, part)
    if cameras:
        ops += camera_ops(model, model.cameras)
    if refs and image_paths:
        ops += reference_ops(model, refs, image_paths)
    return ops


def affected(model: StudioModel, names: Iterable[str]) -> list[ModelPart]:
    """The parts that must be rebuilt when ``names`` change: themselves, their cutters, their hosts' links."""
    names = set(names)
    for part in model.parts:
        if part.role == "cutter" and part.params.get("host") in names:
            names.add(part.name)
        # A Mirror "about" a rebuilt part loses its pivot when that object is deleted.
        if any(mod.get("about") in names for mod in part.modifiers):
            names.add(part.name)
    return [p for p in model.parts if p.name in names]


def rebuild_parts(model: StudioModel, names: Iterable[str], existing: Iterable[str]) -> list[dict]:
    """Delete and recreate just these parts, then relink everything that pointed at them."""
    parts = affected(model, names)
    names = {p.name for p in parts}
    existing = set(existing)
    ops: list[dict] = [{"op": "delete_object", "target": n} for n in names if n in existing]
    for part in sorted(parts, key=_build_order):
        ops += part_ops(model, part)
    for part in model.parts:
        if part.name in names:
            ops += _link_ops(model, part)
        elif part.parent in names:
            ops.append({"op": "set_parent", "child": part.name, "parent": part.parent})
        elif part.role == "cutter" and part.params.get("host") in names:
            ops += _link_ops(model, part)
    return ops


def removed_parts_ops(names: Iterable[str], existing: Iterable[str]) -> list[dict]:
    existing = set(existing)
    return [{"op": "delete_object", "target": n} for n in names if n in existing]
