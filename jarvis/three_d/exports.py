"""Export profiles, and checking what was written — a file existing is not a pass.

Profiles:
  blend  — the project itself (always saved)
  web    — .glb, triangulated, UVs, ≤ 50 000 faces
  game   — .glb, triangulated, UVs, ≤ 20 000 faces (a separate "game-ready copy")
  obj    — .obj interchange
  fbx    — .fbx interchange
  print  — .stl in millimetres, parts unioned; watertight, normals, volume, self-intersection
           and wall-thickness checks, and a print-bed orientation suggestion

``validate_*`` read the written file independently of Blender.
"""
from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .generative import parse_glb

PROFILES = {
    "web": {"format": "glb", "max_faces": 50_000, "suffix": ""},
    "game": {"format": "glb", "max_faces": 20_000, "suffix": "-game"},
    "obj": {"format": "obj", "max_faces": 0, "suffix": ""},
    "fbx": {"format": "fbx", "max_faces": 0, "suffix": ""},
    "print": {"format": "stl", "max_faces": 0, "suffix": "-print"},
}
MIN_WALL_MM = 1.0


@dataclass
class ExportCheck:
    ok: bool
    format: str
    path: str
    faces: int = 0
    size_mm: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return f"{self.format.upper()} checked: {self.faces} faces, " + "×".join(f"{d:.1f}" for d in self.size_mm) + " mm."
        bits = self.problems + self.warnings
        return f"{self.format.upper()} {'has problems' if not self.ok else 'written with warnings'}: " + "; ".join(bits)


# ----------------------------------------------------------------------------- GLB
def validate_glb(path: str, max_faces: int = 0) -> ExportCheck:
    chk = ExportCheck(False, "glb", path)
    try:
        blob = open(path, "rb").read()
        doc, binchunk = parse_glb(blob)
    except (OSError, ValueError) as exc:
        chk.problems.append(f"unreadable: {exc}")
        return chk
    if doc.get("asset", {}).get("version") != "2.0":
        chk.problems.append("not glTF 2.0")
    faces, has_uv, has_normals = 0, True, True
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            has_uv &= "TEXCOORD_0" in attrs
            has_normals &= "NORMAL" in attrs
            acc = doc["accessors"][attrs["POSITION"]]
            if "min" in acc and "max" in acc:
                lo, hi = np.minimum(lo, acc["min"]), np.maximum(hi, acc["max"])
            if "indices" in prim:
                faces += doc["accessors"][prim["indices"]]["count"] // 3
            else:
                faces += acc["count"] // 3
    chk.faces = faces
    if faces == 0:
        chk.problems.append("no geometry")
    if not has_uv:
        chk.problems.append("missing UVs")
    if not has_normals:
        chk.warnings.append("missing normals")
    if max_faces and faces > max_faces * 1.05:
        chk.problems.append(f"{faces} faces is over the {max_faces} budget")
    if np.all(np.isfinite(lo)):
        chk.size_mm = [round(float(v) * 1000, 2) for v in (hi - lo)]       # glTF is metres, y-up
    chk.facts = {"meshes": len(doc.get("meshes", [])), "materials": len(doc.get("materials", [])),
                 "bytes": len(blob), "uv": has_uv}
    chk.ok = not chk.problems
    return chk


# ----------------------------------------------------------------------------- STL
def read_stl(path: str) -> np.ndarray:
    data = open(path, "rb").read()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + n * 50 == len(data):
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                                count=n, offset=84)
            return rec["v"].astype(np.float64)
    text = data.decode("ascii", "ignore")
    if not text.lstrip().startswith("solid"):
        raise ValueError("not an STL file")
    verts = [list(map(float, line.split()[1:4])) for line in text.splitlines() if line.strip().startswith("vertex")]
    return np.array(verts, dtype=np.float64).reshape(-1, 3, 3)


def mesh_topology(tris: np.ndarray, decimals: int = 4) -> dict:
    """Watertightness and orientation from the triangles alone."""
    keys = np.round(tris.reshape(-1, 3), decimals)
    _, inv = np.unique(keys, axis=0, return_inverse=True)
    f = inv.reshape(-1, 3)
    directed = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    und = np.sort(directed, axis=1)
    _, counts = np.unique(und, axis=0, return_counts=True)
    open_edges = int((counts == 1).sum())
    non_manifold = int((counts > 2).sum())
    _, dcounts = np.unique(directed, axis=0, return_counts=True)
    flipped = int((dcounts > 1).sum())            # the same directed edge twice: inconsistent winding
    degenerate = int((np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1) < 1e-12).sum())
    vol = float(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])).sum() / 6.0)
    return {"open_edges": open_edges, "non_manifold_edges": non_manifold, "inconsistent_edges": flipped,
            "degenerate": degenerate, "volume": vol}


def bed_orientation(tris: np.ndarray) -> dict:
    """Which way down: the largest flat area whose normal points the same way."""
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    ok = area > 0
    unit = np.zeros_like(n)
    unit[ok] = n[ok] / (2 * area[ok])[:, None]
    groups: dict[tuple, float] = {}
    for u, a in zip(np.round(unit[ok], 2), area[ok]):
        groups[tuple(u)] = groups.get(tuple(u), 0.0) + a
    if not groups:
        return {}
    down, best = max(groups.items(), key=lambda kv: kv[1])
    axis = int(np.argmax(np.abs(down)))
    sign = 1 if down[axis] > 0 else -1
    words = {(2, -1): "as modelled, flat bottom on the bed", (2, 1): "upside down, top face on the bed",
             (0, -1): "on its left side", (0, 1): "on its right side",
             (1, -1): "lying on its front face", (1, 1): "lying on its back face"}[(axis, sign)]
    tilted = abs(down[axis]) < 0.99
    return {"normal": [float(x) for x in down], "area_mm2": round(float(best), 1),
            "suggestion": words + (" (that face is slightly tilted; check it in the slicer)" if tilted else "")}


def validate_stl(path: str, blender_checks: Optional[dict] = None, expected_mm: Optional[list] = None,
                 tolerance_mm: float = 0.5) -> ExportCheck:
    chk = ExportCheck(False, "stl", path)
    try:
        tris = read_stl(path)
    except (OSError, ValueError) as exc:
        chk.problems.append(f"unreadable: {exc}")
        return chk
    chk.faces = len(tris)
    if not len(tris):
        chk.problems.append("no triangles")
        return chk
    topo = mesh_topology(tris)
    pts = tris.reshape(-1, 3)
    chk.size_mm = [round(float(v), 3) for v in pts.max(axis=0) - pts.min(axis=0)]
    if topo["open_edges"]:
        chk.problems.append(f"not watertight ({topo['open_edges']} open edges)")
    if topo["non_manifold_edges"]:
        chk.problems.append(f"{topo['non_manifold_edges']} non-manifold edges")
    if topo["inconsistent_edges"]:
        chk.problems.append(f"{topo['inconsistent_edges']} edges with inconsistent normals")
    if topo["volume"] <= 0:
        chk.problems.append("normals point inwards (negative volume)")
    if blender_checks:
        si = blender_checks.get("self_intersections", 0)
        if si:
            chk.problems.append(f"{si} self-intersecting face pairs")
        thin = blender_checks.get("min_thickness_m")
        if thin is not None and thin * 1000 < MIN_WALL_MM:
            chk.warnings.append(f"thinnest wall is about {thin * 1000:.2f} mm (under {MIN_WALL_MM} mm may not print)")
    if expected_mm:
        err = max(abs(a - b) for a, b in zip(sorted(chk.size_mm), sorted(expected_mm)))
        chk.facts["scale_error_mm"] = round(err, 3)
        if err > tolerance_mm:
            chk.problems.append(f"size is off by {err:.2f} mm — check the units")
    chk.facts.update(topo)
    chk.facts["volume_cm3"] = round(topo["volume"] / 1000, 3)
    chk.facts["bed"] = bed_orientation(tris)
    chk.ok = not chk.problems
    return chk


# ----------------------------------------------------------------------------- OBJ / FBX
def validate_obj(path: str) -> ExportCheck:
    chk = ExportCheck(False, "obj", path)
    try:
        v = f = 0
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("v "):
                    p = np.array(line.split()[1:4], float)
                    lo, hi = np.minimum(lo, p), np.maximum(hi, p)
                    v += 1
                elif line.startswith("f "):
                    f += 1
    except OSError as exc:
        chk.problems.append(f"unreadable: {exc}")
        return chk
    chk.faces = f
    if not v or not f:
        chk.problems.append("no geometry")
    else:
        chk.size_mm = [round(float(x) * 1000, 2) for x in hi - lo]
    chk.ok = not chk.problems
    return chk


def validate_fbx(path: str) -> ExportCheck:
    chk = ExportCheck(False, "fbx", path)
    try:
        head = open(path, "rb").read(23)
    except OSError as exc:
        chk.problems.append(f"unreadable: {exc}")
        return chk
    if not head.startswith(b"Kaydara FBX Binary"):
        chk.problems.append("not a binary FBX file")
    chk.ok = not chk.problems
    return chk


VALIDATORS = {"glb": validate_glb, "stl": validate_stl, "obj": validate_obj, "fbx": validate_fbx}
