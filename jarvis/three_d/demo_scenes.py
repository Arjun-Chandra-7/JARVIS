"""Ground-truth renders for tests and demonstrations, made by the same restricted bridge.

``render_product`` builds a known object in a throwaway headless Blender, renders it from a
perspective camera with Workbench, and returns the image path and the truth. The truth is for
the test harness only; the reconstruction never sees it.
"""
from __future__ import annotations

import math
import os

from . import scene_ops, synthetic
from .blender_bridge import BlenderSession


def render_product(out_dir: str, *, elevation_deg: float = 12.0, height_mm: float = 180.0,
                   resolution: int = 512) -> dict:
    prof = synthetic.vase_profile(height_mm)
    MM = 0.001
    d = height_mm * 4 * MM
    el = math.radians(elevation_deg)
    c = [0.0, 0.0, height_mm / 2 * MM]
    plan = [
        {"op": "create_revolve", "name": "Truth", "profile": [[r * MM, z * MM] for r, z in prof], "segments": 128},
        {"op": "assign_material", "target": "Truth", "material": "Glaze", "color": [0.2, 0.45, 0.7], "roughness": 0.3},
        {"op": "add_camera", "name": "Shot", "kind": "perspective", "lens": 50.0,
         "location": [0.0, -d * math.cos(el), c[2] + d * math.sin(el)], "look_at": c},
    ]
    path = os.path.join(out_dir, "product.png")
    with BlenderSession([out_dir]) as b:
        scene_ops.validate_plan(plan)
        b.call("new_project")                 # not Blender's default cube behind the object
        b.apply(plan, known=[])
        b.render("Shot", path, resolution=resolution, mode="shaded")
    return {"path": path, "profile": prof, "height_mm": height_mm, "elevation_deg": elevation_deg}


def render_turntable(out_dir: str, frames: int = 8, resolution: int = 256) -> dict:
    """A box-and-cylinder object rendered from evenly spaced angles, orthographic, level camera."""
    MM = 0.001
    plan = [
        {"op": "create_primitive", "name": "Body", "shape": "box", "size": [0.06, 0.04, 0.05], "location": [0, 0, 0.025]},
        {"op": "create_primitive", "name": "Knob", "shape": "cylinder", "size": [0.02, 0.02, 0.02],
         "location": [0.015, 0.008, 0.06], "segments": 48},
        {"op": "assign_material", "target": "Body", "material": "Paint", "color": [0.8, 0.3, 0.2]},
        {"op": "assign_material", "target": "Knob", "material": "Paint", "color": [0.8, 0.3, 0.2]},
    ]
    paths = []
    with BlenderSession([out_dir]) as b:
        b.call("new_project")
        b.apply(plan, known=[])
        for i in range(frames):
            a = 2 * math.pi * i / frames
            name = f"Cam {i}"
            # The object turns by +a; equivalently the camera orbits by -a around it.
            b.apply([{"op": "add_camera", "name": name, "kind": "orthographic", "ortho_scale": 0.12,
                      "location": [-0.5 * math.sin(a), -0.5 * math.cos(a), 0.035],
                      "look_at": [0.0, 0.0, 0.035]}])
            p = os.path.join(out_dir, f"turn-{i:02d}.png")
            b.render(name, p, resolution=resolution, mode="shaded")
            paths.append(p)
    return {"paths": paths, "size_mm": (60.0, 40.0, 70.0)}
