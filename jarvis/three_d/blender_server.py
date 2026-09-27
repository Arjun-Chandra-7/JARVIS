"""The restricted server JARVIS runs *inside* Blender.

Started as ``blender [-b] [file.blend] --disable-autoexec --python blender_server.py -- ARGS``.

Trust boundary
--------------
* A Unix socket in a 0700 directory, itself 0600. No TCP, nothing on the network.
* Every connection must first say ``hello`` with the session token. The token arrives in a 0600
  file that this script reads and **deletes** at start, so no later process can read it.
* The peer's credentials (SO_PEERCRED) must be this user, and — when the launcher named them —
  one of the allowed process ids. A same-user process that somehow had the token but is not the
  JARVIS process is still refused.
* Requests name one of a fixed set of commands. There is no "run this Python", no shell, no
  operator passthrough. Scene edits are ``scene_ops`` plans, validated here again.
* Paths are resolved (symlinks followed) and must be inside the project roots given at launch.
* Only objects this server created (``jarvis_owned``) can be changed or deleted, so the user's
  own objects in a scene are never touched.
* ``bpy`` is only touched on Blender's main thread. The socket threads answer ``ping`` and
  ``cancel`` themselves, so a long operation can be cancelled and a hung one is noticed.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import json
import math
import os
import queue
import socket
import struct
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from jarvis.three_d import scene_ops  # noqa: E402  (standard library only)

import bpy  # noqa: E402
import bmesh  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

MAX_MSG = 48 * 1024 * 1024
MIN_VERSION = (4, 2, 0)
MAX_VERSION = (6, 0, 0)
PROTOCOL = 1

STATE = {
    "token": b"",
    "allowed_pids": set(),
    "roots": [],
    "owner": "",
    "cancel": threading.Event(),
    "busy": "",
    "tick": time.time(),
    "stop": threading.Event(),
    "started": time.time(),
    "max_render_px": 1024,
}
WORK: "queue.Queue[tuple[dict, queue.Queue]]" = queue.Queue()


class Refused(Exception):
    """A request was not allowed. The message is safe to send back."""


# ============================================================================ wire
def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def recv_msg(conn: socket.socket) -> dict:
    (n,) = struct.unpack(">I", _recv_exact(conn, 4))
    if n > MAX_MSG:
        raise Refused("message too large")
    data = json.loads(_recv_exact(conn, n).decode("utf-8"))
    if not isinstance(data, dict):
        raise Refused("message must be an object")
    return data


def send_msg(conn: socket.socket, data: dict) -> None:
    blob = json.dumps(data, separators=(",", ":"), default=str).encode("utf-8")
    conn.sendall(struct.pack(">I", len(blob)) + blob)


def peer(conn: socket.socket) -> tuple[int, int, int]:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)       # pid, uid, gid


# ============================================================================ connections
def handle_connection(conn: socket.socket) -> None:
    try:
        pid, uid, _gid = peer(conn)
        if uid != os.getuid():
            send_msg(conn, {"ok": False, "error": "foreign user"})
            return
        if STATE["allowed_pids"] and pid not in STATE["allowed_pids"]:
            send_msg(conn, {"ok": False, "error": "foreign process"})
            return
        conn.settimeout(15)
        hello = recv_msg(conn)
        token = str(hello.get("token", "")).encode()
        if hello.get("cmd") != "hello" or not hmac.compare_digest(token, STATE["token"]):
            send_msg(conn, {"ok": False, "error": "not authenticated"})
            return
        send_msg(conn, {"ok": True, "protocol": PROTOCOL, "version": list(bpy.app.version),
                        "version_string": bpy.app.version_string, "schema": scene_ops.SCHEMA_VERSION,
                        "background": bool(bpy.app.background), "owner": STATE["owner"]})
        conn.settimeout(None)
        while not STATE["stop"].is_set():
            try:
                req = recv_msg(conn)
            except (ConnectionError, OSError):
                return
            except (Refused, ValueError) as exc:
                send_msg(conn, {"ok": False, "error": str(exc)[:200]})
                continue
            cmd = req.get("cmd")
            rid = req.get("id")
            if cmd == "ping":
                send_msg(conn, {"ok": True, "id": rid, "busy": STATE["busy"],
                                "main_idle_s": round(time.time() - STATE["tick"], 2),
                                "file": bpy.data.filepath if not STATE["busy"] else ""})
                continue
            if cmd == "cancel":
                STATE["cancel"].set()
                send_msg(conn, {"ok": True, "id": rid})
                continue
            if cmd not in COMMANDS:
                send_msg(conn, {"ok": False, "id": rid, "error": "unknown command"})
                continue
            reply: queue.Queue = queue.Queue(maxsize=1)
            WORK.put((req, reply))
            while True:
                try:
                    result = reply.get(timeout=0.5)
                    break
                except queue.Empty:
                    if STATE["stop"].is_set():
                        result = {"ok": False, "error": "shutting down"}
                        break
            result["id"] = rid
            send_msg(conn, result)
            if cmd == "shutdown":
                return
    except Exception:  # noqa: BLE001 — a broken client must not take the server down
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def serve(sock_path: str) -> None:
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        srv.bind(sock_path)
    finally:
        os.umask(old)
    os.chmod(sock_path, 0o600)
    srv.listen(8)
    srv.settimeout(0.5)
    while not STATE["stop"].is_set():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=handle_connection, args=(conn,), daemon=True).start()
    srv.close()
    try:
        os.unlink(sock_path)
    except OSError:
        pass


def run_one() -> None:
    """Run queued work on the main thread."""
    STATE["tick"] = time.time()
    while True:
        try:
            req, reply = WORK.get_nowait()
        except queue.Empty:
            return
        cmd = req.get("cmd")
        STATE["busy"] = cmd
        STATE["cancel"].clear()
        try:
            result = COMMANDS[cmd](req.get("args") or {})
            result = {"ok": True, **(result or {})}
        except scene_ops.PlanError as exc:
            result = {"ok": False, "error": str(exc), "category": "invalid_scene_operation"}
        except Refused as exc:
            result = {"ok": False, "error": str(exc)[:300], "category": "refused"}
        except Cancelled:
            result = {"ok": False, "error": "cancelled", "category": "cancelled"}
        except MemoryError:
            result = {"ok": False, "error": "out of memory", "category": "resource_limit"}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                      "category": "internal_error", "trace": traceback.format_exc()[-800:]}
        finally:
            STATE["busy"] = ""
            STATE["tick"] = time.time()
        reply.put(result)


class Cancelled(Exception):
    pass


def check_cancel() -> None:
    if STATE["cancel"].is_set():
        raise Cancelled()


# ============================================================================ ownership
def owned(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise Refused("object does not exist")
    if not obj.get("jarvis_owned"):
        raise Refused("object is not part of this JARVIS project")
    return obj


def owned_objects():
    return [o for o in bpy.data.objects if o.get("jarvis_owned")]


def _collection(name: str | None):
    scene = bpy.context.scene
    if not name:
        return scene.collection
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        col["jarvis_owned"] = True
        scene.collection.children.link(col)
    return col


def _adopt(obj, collection: str | None, kind: str = "part"):
    _collection(collection).objects.link(obj)
    obj["jarvis_owned"] = True
    obj["jarvis_owner"] = STATE["owner"]
    obj["jarvis_role"] = kind
    return obj


def _new_object(name: str, data, collection: str | None, kind: str = "part"):
    if bpy.data.objects.get(name) is not None:
        old = bpy.data.objects[name]
        if not old.get("jarvis_owned"):
            raise Refused("name is taken by an object JARVIS does not own")
        bpy.data.objects.remove(old, do_unlink=True)
    return _adopt(bpy.data.objects.new(name, data), collection, kind)


def _m(v):
    return Vector((float(v[0]), float(v[1]), float(v[2])))


# ============================================================================ scene ops
def op_create_collection(a):
    col = bpy.data.collections.get(a["name"])
    if col is None:
        col = bpy.data.collections.new(a["name"])
        col["jarvis_owned"] = True
        parent = bpy.data.collections.get(a["parent"]) if a.get("parent") else None
        (parent.children if parent else bpy.context.scene.collection.children).link(col)


def _torus(bm, R, r, seg):
    rings = []
    minor = max(8, seg // 3)
    for i in range(seg):
        a = 2 * math.pi * i / seg
        ring = []
        for j in range(minor):
            b = 2 * math.pi * j / minor
            ring.append(bm.verts.new(((R + r * math.cos(b)) * math.cos(a),
                                      (R + r * math.cos(b)) * math.sin(a), r * math.sin(b))))
        rings.append(ring)
    for i in range(seg):
        for j in range(minor):
            bm.faces.new((rings[i][j], rings[(i + 1) % seg][j], rings[(i + 1) % seg][(j + 1) % minor],
                          rings[i][(j + 1) % minor]))


def op_create_primitive(a):
    sx, sy, sz = a["size"]
    seg = a.get("segments", 48)
    bm = bmesh.new()
    shape = a["shape"]
    if shape == "box":
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    elif shape in ("cylinder", "cone"):
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg, radius1=0.5,
                              radius2=0.5 if shape == "cylinder" else 0.0, depth=1.0)
        bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    elif shape == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=max(8, seg // 2), radius=0.5)
        bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    elif shape == "torus":
        _torus(bm, 0.5 - sz / 2, sz / 2, seg)
        bmesh.ops.scale(bm, vec=Vector((sx, sy, 1.0)), verts=bm.verts)
    elif shape == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
        bmesh.ops.scale(bm, vec=Vector((sx, sy, 1.0)), verts=bm.verts)
    mesh = bpy.data.meshes.new(a["name"])
    bm.to_mesh(mesh)
    bm.free()
    obj = _new_object(a["name"], mesh, a.get("collection"))
    if a.get("top_group"):
        group = obj.vertex_groups.new(name="top")
        top = max(v.co.z for v in mesh.vertices)
        group.add([v.index for v in mesh.vertices if abs(v.co.z - top) < 1e-7], 1.0, "REPLACE")
    obj.location = _m(a.get("location", (0, 0, 0)))
    obj.rotation_euler = a.get("rotation", (0, 0, 0))


def op_create_curve(a):
    cu = bpy.data.curves.new(a["name"], type="CURVE")
    cu.dimensions = "2D"
    cu.fill_mode = "BOTH"
    cu.resolution_u = 4
    for poly in a["polygons"]:
        for ring in [poly["outer"], *(poly.get("holes") or [])]:
            if a.get("smooth"):
                sp = cu.splines.new("BEZIER")
                sp.bezier_points.add(len(ring) - 1)
                for bp, (x, y) in zip(sp.bezier_points, ring):
                    bp.co = (x, y, 0.0)
                    bp.handle_left_type = bp.handle_right_type = "AUTO"
            else:
                sp = cu.splines.new("POLY")
                sp.points.add(len(ring) - 1)
                for p, (x, y) in zip(sp.points, ring):
                    p.co = (x, y, 0.0, 1.0)
            sp.use_cyclic_u = True
        check_cancel()
    obj = _new_object(a["name"], cu, a.get("collection"))
    obj.location = _m(a.get("location", (0, 0, 0)))
    obj.rotation_euler = a.get("rotation", (0, 0, 0))


def _signed_volume(obj) -> float:
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(me)
    vol = bm.calc_volume(signed=True)
    bm.free()
    ev.to_mesh_clear()
    return vol


def op_create_revolve(a):
    prof = a["profile"]
    mesh = bpy.data.meshes.new(a["name"])
    verts = [(float(r), 0.0, float(z)) for r, z in prof]
    edges = [(i, i + 1) for i in range(len(verts) - 1)]
    mesh.from_pydata(verts, edges, [])
    obj = _new_object(a["name"], mesh, a.get("collection"))
    mod = obj.modifiers.new("Revolve", "SCREW")
    mod.angle = 2 * math.pi
    mod.steps = mod.render_steps = a.get("segments", 64)
    mod.axis = "Z"
    mod.use_merge_vertices = True
    mod.merge_threshold = 1e-5
    mod.use_normal_calculate = True
    obj.location = _m(a.get("location", (0, 0, 0)))
    if _signed_volume(obj) < 0:
        mod.use_normal_flip = True


def op_create_mesh(a):
    mesh = bpy.data.meshes.new(a["name"])
    mesh.from_pydata([tuple(v) for v in a["vertices"]], [], [tuple(f) for f in a["faces"]])
    mesh.validate()
    obj = _new_object(a["name"], mesh, a.get("collection"))
    obj.location = _m(a.get("location", (0, 0, 0)))


def op_extrude_profile(a):
    obj = owned(a["target"])
    if obj.type != "CURVE":
        raise Refused("only curve profiles can be extruded")
    obj.data.extrude = a["depth"] / 2.0
    obj.data.offset = a.get("offset", obj.data.offset)


def op_bevel(a):
    obj = owned(a["target"])
    if obj.type == "CURVE":
        obj.data.bevel_mode = "ROUND"
        obj.data.bevel_depth = a["width"]
        obj.data.bevel_resolution = a.get("segments", 3)
        obj.data.offset = -a["width"]           # keep the outline where the reference put it
        return
    mod = obj.modifiers.get(a.get("name", "Bevel")) or obj.modifiers.new(a.get("name", "Bevel"), "BEVEL")
    mod.width = a["width"]
    mod.segments = a.get("segments", 3)
    mod.limit_method = "ANGLE"
    if a.get("only_group"):
        if obj.vertex_groups.get(a["only_group"]) is None:
            raise Refused("that part has no top edges group")
        mod.limit_method = "VGROUP"
        mod.vertex_group = a["only_group"]


def op_boolean(a):
    obj = owned(a["target"])
    cutter = owned(a["cutter"])
    name = a.get("name") or f"Boolean {cutter.name}"[:63]
    mod = obj.modifiers.get(name) or obj.modifiers.new(name, "BOOLEAN")
    mod.object = cutter
    mod.operation = a["operation"].upper()
    mod.solver = "EXACT"
    if a.get("hide_cutter", True):
        cutter.display_type = "WIRE"
        cutter.hide_render = True
        cutter["jarvis_role"] = "cutter"


def op_mirror(a):
    obj = owned(a["target"])
    mod = obj.modifiers.get(a.get("name", "Mirror")) or obj.modifiers.new(a.get("name", "Mirror"), "MIRROR")
    mod.use_axis = [a["axis"] == ax for ax in "xyz"]
    if a.get("mirror_object"):
        mod.mirror_object = owned(a["mirror_object"])


def op_array(a):
    obj = owned(a["target"])
    mod = obj.modifiers.get(a.get("name", "Array")) or obj.modifiers.new(a.get("name", "Array"), "ARRAY")
    mod.count = a["count"]
    mod.use_relative_offset = False
    mod.use_constant_offset = True
    mod.constant_offset_displace = _m(a["offset"])


def op_subdivision(a):
    obj = owned(a["target"])
    mod = obj.modifiers.get(a.get("name", "Subdivision")) or obj.modifiers.new(a.get("name", "Subdivision"), "SUBSURF")
    mod.levels = mod.render_levels = a["levels"]


def op_solidify(a):
    obj = owned(a["target"])
    mod = obj.modifiers.get(a.get("name", "Solidify")) or obj.modifiers.new(a.get("name", "Solidify"), "SOLIDIFY")
    mod.thickness = a["thickness"]


def op_remesh(a):
    obj = owned(a["target"])
    mod = obj.modifiers.get(a.get("name", "Remesh")) or obj.modifiers.new(a.get("name", "Remesh"), "REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = a["voxel_size"]


def op_set_transform(a):
    obj = owned(a["target"])
    if "location" in a:
        obj.location = _m(a["location"])
    if "rotation" in a:
        obj.rotation_euler = a["rotation"]
    if "scale" in a:
        obj.scale = _m(a["scale"])


def op_set_dimensions(a):
    owned(a["target"]).dimensions = _m(a["dimensions"])


def op_assign_material(a):
    obj = owned(a["target"])
    mat = bpy.data.materials.get(a["material"])
    if mat is None:
        mat = bpy.data.materials.new(a["material"])
        mat["jarvis_owned"] = True
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if "color" in a:
        mat.diffuse_color = (*a["color"], 1.0)
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*a["color"], 1.0)
    if "metallic" in a:
        mat.metallic = a["metallic"]
        if bsdf:
            bsdf.inputs["Metallic"].default_value = a["metallic"]
    if "roughness" in a:
        mat.roughness = a["roughness"]
        if bsdf:
            bsdf.inputs["Roughness"].default_value = a["roughness"]
    if obj.data is not None and hasattr(obj.data, "materials"):
        obj.data.materials.clear()
        obj.data.materials.append(mat)


def op_set_parent(a):
    child, parent = owned(a["child"]), owned(a["parent"])
    # A just-created object's matrix_world is stale until the depsgraph runs; without this the
    # child would keep an identity "world" transform and jump to the origin.
    bpy.context.view_layer.update()
    world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    child.matrix_world = world


def op_set_properties(a):
    obj = owned(a["target"])
    for k, v in a["props"].items():
        obj[k] = v


def op_set_visibility(a):
    obj = owned(a["target"])
    obj.hide_render = not a["visible"]
    obj.hide_viewport = not a["visible"]


def _look(obj, target):
    direction = _m(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def op_add_camera(a):
    cam = bpy.data.cameras.new(a["name"])
    if a.get("kind", "perspective") == "orthographic":
        cam.type = "ORTHO"
        cam.ortho_scale = a.get("ortho_scale", 1.0)
    else:
        cam.lens = a.get("lens", 50.0)
    cam.clip_start = 0.001
    cam.clip_end = 500.0
    obj = _new_object(a["name"], cam, a.get("collection"), "camera")
    obj.location = _m(a["location"])
    bpy.context.view_layer.update()
    _look(obj, a["look_at"])
    obj["jarvis_view"] = a.get("view", "unknown")


def op_add_light(a):
    light = bpy.data.lights.new(a["name"], a["kind"].upper())
    light.energy = a.get("energy", 500.0)
    if a["kind"] == "area":
        light.size = a.get("size", 1.0)
    obj = _new_object(a["name"], light, a.get("collection"), "light")
    obj.location = _m(a["location"])
    if a.get("look_at"):
        _look(obj, a["look_at"])


def op_add_reference_plane(a):
    path = scene_ops.safe_path(a["image"], STATE["roots"], must_exist=True)
    img = bpy.data.images.load(path, check_existing=True)
    w, h = a["size"]
    mesh = bpy.data.meshes.new(a["name"])
    mesh.from_pydata([(-w / 2, 0, -h / 2), (w / 2, 0, -h / 2), (w / 2, 0, h / 2), (-w / 2, 0, h / 2)], [],
                     [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new()
    for loop, co in zip(uv.data, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop.uv = co
    mat = bpy.data.materials.new(f"{a['name']} image")
    mat["jarvis_owned"] = True
    mat.use_nodes = True
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf:
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Alpha"].default_value = a.get("opacity", 0.6)
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    mesh.materials.append(mat)
    obj = _new_object(a["name"], mesh, a.get("collection"), "reference")
    obj.location = _m(a.get("location", (0, 0, 0)))
    rot = {"front": (0, 0, 0), "back": (0, 0, math.pi), "side": (0, 0, math.pi / 2),
           "top": (-math.pi / 2, 0, 0)}.get(a["view"], (0, 0, 0))
    obj.rotation_euler = rot
    obj.hide_render = True
    obj.hide_select = True
    obj["jarvis_image"] = os.path.basename(path)


def op_delete_object(a):
    obj = owned(a["target"])
    bpy.data.objects.remove(obj, do_unlink=True)


SCENE_OPS = {name[3:]: fn for name, fn in globals().items() if name.startswith("op_")}
assert set(SCENE_OPS) == set(scene_ops.OPS), set(SCENE_OPS) ^ set(scene_ops.OPS)


# ============================================================================ commands
def _setup_scene():
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"
    try:
        bpy.context.preferences.filepaths.save_version = 0
    except Exception:  # noqa: BLE001
        pass


def cmd_new_project(args):
    """Start from an empty scene (never the user's open file: a new file in this instance)."""
    bpy.ops.wm.read_homefile(use_empty=True)
    _setup_scene()
    return {"objects": 0}


def cmd_clear_project(args):
    for obj in owned_objects():
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in [c for c in bpy.data.collections if c.get("jarvis_owned")]:
        if not col.objects and not col.children:
            bpy.data.collections.remove(col)
    return {}


def cmd_apply(args):
    plan = args.get("plan")
    known = [o.name for o in owned_objects()] + [c.name for c in bpy.data.collections if c.get("jarvis_owned")]
    report = scene_ops.validate_plan(plan, known=known, image_roots=STATE["roots"])
    done = 0
    for i, op in enumerate(plan):
        check_cancel()
        fn = SCENE_OPS[op["op"]]
        try:
            fn({k: v for k, v in op.items() if k != "op"})
        except (Refused, scene_ops.PlanError) as exc:
            raise Refused(f"operation {i} ({op['op']}) refused: {exc}") from None
        done += 1
    bpy.context.view_layer.update()
    return {"applied": done, "faces_estimate": report.faces}


def _bounds(obj, dg):
    ev = obj.evaluated_get(dg)
    pts = []
    if obj.type in ("MESH", "CURVE"):
        # An evaluated curve's bound_box is Blender's default unit box; measure the real geometry.
        me = ev.to_mesh()
        mw = ev.matrix_world
        pts = [mw @ v.co for v in me.vertices]
        ev.to_mesh_clear()
    if not pts:
        pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return lo, hi


def cmd_scene(args):
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for obj in owned_objects():
        lo, hi = _bounds(obj, dg)
        info = {"name": obj.name, "type": obj.type, "role": obj.get("jarvis_role", "part"),
                "parent": obj.parent.name if obj.parent else "",
                "children": [c.name for c in obj.children],
                "location": list(obj.location), "rotation": list(obj.rotation_euler), "scale": list(obj.scale),
                "bounds": [lo, hi], "size": [hi[i] - lo[i] for i in range(3)],
                "modifiers": [{"name": m.name, "type": m.type} for m in obj.modifiers],
                "materials": [m.name for m in getattr(obj.data, "materials", []) if m] if obj.data else [],
                "visible": not obj.hide_render,
                "collection": obj.users_collection[0].name if obj.users_collection else "",
                "props": {k: obj[k] for k in obj.keys() if k.startswith("jarvis_") and isinstance(obj[k], (str, int, float))}}
        if obj.type == "MESH" and obj.data.vertices:
            # The part's own geometry, before modifiers: what a parameter edit must have changed.
            vs = obj.data.vertices
            info["data_size"] = [(max(v.co[i] for v in vs) - min(v.co[i] for v in vs)) * abs(obj.scale[i])
                                 for i in range(3)]
        if obj.type in ("MESH", "CURVE"):
            ev = obj.evaluated_get(dg)
            me = ev.to_mesh()
            info["verts"] = len(me.vertices)
            info["faces"] = len(me.polygons)
            ev.to_mesh_clear()
        out.append(info)
    mats = [{"name": m.name, "color": list(m.diffuse_color)[:3], "metallic": m.metallic, "roughness": m.roughness}
            for m in bpy.data.materials if m.get("jarvis_owned")]
    return {"objects": out, "materials": mats, "file": bpy.data.filepath,
            "unit": bpy.context.scene.unit_settings.length_unit}


def _part_objects(names):
    if names:
        return [owned(n) for n in names]
    return [o for o in owned_objects() if o.get("jarvis_role", "part") == "part" and not o.hide_render
            and o.type in ("MESH", "CURVE")]


def cmd_snapshot(args):
    """World-space triangles of the evaluated parts — what validation rasterises."""
    max_faces = min(int(args.get("max_faces", 200_000)), 400_000)
    dg = bpy.context.evaluated_depsgraph_get()
    import array
    tris = array.array("f")
    per = []
    total = 0
    for obj in _part_objects(args.get("names") or []):
        check_cancel()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        mw = ev.matrix_world
        co = [mw @ v.co for v in me.vertices]
        start = total
        h = 0.0
        for t in me.loop_triangles:
            if total >= max_faces:
                break
            for vi in t.vertices:
                p = co[vi]
                tris.extend((p.x, p.y, p.z))
                h += p.x * 1.3 + p.y * 1.7 + p.z * 1.9
            total += 1
        per.append({"name": obj.name, "start": start, "count": total - start,
                    "geometry_hash": round(h, 6), "verts": len(me.vertices)})
        ev.to_mesh_clear()
    return {"triangles": base64.b64encode(tris.tobytes()).decode(), "count": total, "objects": per,
            "truncated": total >= max_faces}


def _hide_non_parts(keep):
    hidden = []
    for obj in bpy.context.scene.objects:
        if obj not in keep and not obj.hide_render and obj.type in ("MESH", "CURVE"):
            obj.hide_render = True
            hidden.append(obj)
    return hidden


def cmd_render(args):
    """A Workbench preview from one of our cameras. Bounded size; no Cycles, no compositing."""
    scene = bpy.context.scene
    cam = owned(args["camera"])
    if cam.type != "CAMERA":
        raise Refused("that is not a camera")
    path = scene_ops.safe_path(args["path"], STATE["roots"])
    res = int(args.get("resolution", 384))
    if not (16 <= res <= STATE["max_render_px"]):
        raise Refused("render resolution out of bounds")
    mode = args.get("mode", "shaded")
    if mode not in ("shaded", "mask", "material"):
        raise Refused("unknown render mode")
    saved = (scene.camera, scene.render.engine, scene.render.filepath, scene.render.resolution_x,
             scene.render.resolution_y, scene.render.resolution_percentage, scene.render.film_transparent)
    shading = scene.display.shading
    saved_shading = (shading.light, shading.color_type, tuple(shading.single_color), shading.show_shadows)
    world = scene.world or bpy.data.worlds.new("JARVIS World")
    scene.world = world
    saved_world = tuple(world.color)
    hidden = _hide_non_parts(_part_objects([]) if mode == "mask" else list(scene.objects))
    try:
        scene.camera = cam
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x = int(res * args.get("aspect", 1.0)) if args.get("aspect") else res
        scene.render.resolution_y = res
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = False
        scene.render.filepath = path
        scene.render.image_settings.file_format = "PNG"
        if mode == "mask":
            shading.light = "FLAT"
            shading.color_type = "SINGLE"
            shading.single_color = (1, 1, 1)
            shading.show_shadows = False
            world.color = (0, 0, 0)
            scene.view_settings.view_transform = "Standard"
        else:
            shading.light = "STUDIO"
            shading.color_type = "MATERIAL"
            world.color = (0.05, 0.05, 0.06)
        t = time.time()
        bpy.ops.render.render(write_still=True)
        return {"path": path, "seconds": round(time.time() - t, 3)}
    finally:
        for obj in hidden:
            obj.hide_render = False
        (scene.camera, scene.render.engine, scene.render.filepath, scene.render.resolution_x,
         scene.render.resolution_y, scene.render.resolution_percentage, scene.render.film_transparent) = saved
        shading.light, shading.color_type, sc, shading.show_shadows = saved_shading
        shading.single_color = sc
        world.color = saved_world


def cmd_save(args):
    """Save atomically: write a copy beside the target, then rename over it."""
    path = scene_ops.safe_path(args["path"], STATE["roots"])
    if not path.endswith(".blend"):
        raise Refused("projects are saved as .blend")
    tmp = path + ".saving.blend"
    try:
        bpy.ops.wm.save_as_mainfile(filepath=tmp, copy=True, check_existing=False, compress=True)
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 100:
            raise Refused("save produced no file")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {"path": path, "bytes": os.path.getsize(path)}


def cmd_open(args):
    path = scene_ops.safe_path(args["path"], STATE["roots"], must_exist=True)
    if not path.endswith(".blend"):
        raise Refused("only .blend projects can be opened")
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=not bpy.app.background, use_scripts=False)
    _setup_scene()
    return {"path": path, "objects": len(owned_objects())}


def _box_uv(me):
    """Cube-projection UVs: deterministic, needs no editor context."""
    uv = me.uv_layers.new(name="UVMap") if not me.uv_layers else me.uv_layers[0]
    for poly in me.polygons:
        n = poly.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        u_i, v_i = [(1, 2), (0, 2), (0, 1)][ax]
        for li in poly.loop_indices:
            co = me.vertices[me.loops[li].vertex_index].co
            uv.data[li].uv = (co[u_i], co[v_i])


def _export_meshes(names, *, triangulate=False, decimate_to=0, union=False):
    """Temporary mesh copies of the evaluated parts, in world space. Caller deletes them."""
    dg = bpy.context.evaluated_depsgraph_get()
    temps = []
    col = _collection("JARVIS Export (temporary)")
    for obj in _part_objects(names):
        ev = obj.evaluated_get(dg)
        me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
        me.transform(ev.matrix_world)
        tmp = bpy.data.objects.new(f"export {obj.name}"[:63], me)
        col.objects.link(tmp)
        tmp["jarvis_owned"] = True
        tmp["jarvis_role"] = "export"
        temps.append(tmp)
    if union and len(temps) > 1:
        base = temps[0]
        for other in temps[1:]:
            mod = base.modifiers.new(f"U {other.name}"[:63], "BOOLEAN")
            mod.operation = "UNION"
            mod.solver = "EXACT"
            mod.object = other
        dg = bpy.context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(base.evaluated_get(dg), depsgraph=dg)
        merged = bpy.data.objects.new("export union", me)
        col.objects.link(merged)
        merged["jarvis_owned"] = True
        for t in temps:
            bpy.data.objects.remove(t, do_unlink=True)
        temps = [merged]
    for tmp in temps:
        bm = bmesh.new()
        bm.from_mesh(tmp.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        if triangulate:
            bmesh.ops.triangulate(bm, faces=bm.faces)
        bm.to_mesh(tmp.data)
        bm.free()
    if decimate_to:
        total = sum(len(t.data.polygons) for t in temps)
        if total > decimate_to:
            ratio = max(0.02, decimate_to / total)
            for tmp in temps:
                mod = tmp.modifiers.new("Decimate", "DECIMATE")
                mod.ratio = ratio
            dg = bpy.context.evaluated_depsgraph_get()
            for tmp in temps:
                me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg), depsgraph=dg)
                tmp.modifiers.clear()
                old = tmp.data
                tmp.data = me
                bpy.data.meshes.remove(old)
    for tmp in temps:
        if not tmp.data.uv_layers:
            _box_uv(tmp.data)
    return temps, col


def _mesh_checks(objs, thickness_samples=600):
    bm = bmesh.new()
    for obj in objs:
        bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    wrong_winding = sum(1 for e in bm.edges if e.is_manifold and not e.is_contiguous)
    volume = bm.calc_volume(signed=True)
    tree = BVHTree.FromBMesh(bm, epsilon=0.0)
    pairs = tree.overlap(tree)
    faces = bm.faces
    intersections = 0
    for a, b in pairs:
        if a >= b:
            continue
        va = {v.index for v in faces[a].verts}
        if va & {v.index for v in faces[b].verts}:
            continue
        intersections += 1
    thinnest = math.inf
    step = max(1, len(faces) // thickness_samples) if thickness_samples else 0
    for f in (list(faces)[::step] if step else []):
        origin = f.calc_center_median() - f.normal * 1e-6
        hit = tree.ray_cast(origin, -f.normal)
        if hit[0] is not None and hit[3] is not None and hit[3] > 1e-6:
            thinnest = min(thinnest, hit[3])
    lo = [min(v.co[i] for v in bm.verts) for i in range(3)] if bm.verts else [0, 0, 0]
    hi = [max(v.co[i] for v in bm.verts) for i in range(3)] if bm.verts else [0, 0, 0]
    out = {"faces": len(faces), "non_manifold_edges": non_manifold, "boundary_edges": boundary,
           "inconsistent_normals": wrong_winding, "self_intersections": intersections,
           "volume_m3": volume, "min_thickness_m": thinnest if math.isfinite(thinnest) else None,
           "size_m": [hi[i] - lo[i] for i in range(3)]}
    bm.free()
    return out


def cmd_export(args):
    fmt = args.get("format")
    if fmt not in ("glb", "gltf", "obj", "fbx", "stl"):
        raise Refused("unsupported export format")
    path = scene_ops.safe_path(args["path"], STATE["roots"])
    if not path.lower().endswith("." + fmt):
        raise Refused("file extension must match the format")
    budget = int(args.get("max_faces", 0) or 0)
    if budget and not (100 <= budget <= 400_000):
        raise Refused("polygon budget out of range")
    for_print = fmt == "stl"
    temps, col = _export_meshes(args.get("names") or [], triangulate=fmt in ("stl", "glb", "gltf"),
                                decimate_to=budget, union=for_print and args.get("union", True))
    remeshed = 0.0
    try:
        if for_print and temps:
            first = _mesh_checks(temps, thickness_samples=0)
            if first["non_manifold_edges"] or first["boundary_edges"] or first["self_intersections"]:
                # The print copy only: rebuild it as one watertight voxel solid, fine enough for a nozzle.
                size = max(first["size_m"]) or 0.1
                remeshed = min(max(size / 600.0, 0.0001), 0.001)
                tmpo = temps[0]
                mod = tmpo.modifiers.new("Print remesh", "REMESH")
                mod.mode = "VOXEL"
                mod.voxel_size = remeshed
                mod.adaptivity = 0.0
                dg = bpy.context.evaluated_depsgraph_get()
                me = bpy.data.meshes.new_from_object(tmpo.evaluated_get(dg), depsgraph=dg)
                tmpo.modifiers.clear()
                old = tmpo.data
                tmpo.data = me
                bpy.data.meshes.remove(old)
                bm = bmesh.new()
                bm.from_mesh(me)
                bmesh.ops.triangulate(bm, faces=bm.faces)
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
                bm.to_mesh(me)
                bm.free()
                # Voxel faces on flat areas carry no shape: merge coplanar ones — but only keep that
                # when the lighter mesh is still clean.
                light = me.copy()
                bm = bmesh.new()
                bm.from_mesh(light)
                bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(0.5), verts=bm.verts, edges=bm.edges)
                bmesh.ops.triangulate(bm, faces=bm.faces, quad_method="BEAUTY", ngon_method="BEAUTY")
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
                bm.to_mesh(light)
                bm.free()
                tmpo.data = light
                c = _mesh_checks([tmpo], thickness_samples=0)
                if c["self_intersections"] or c["non_manifold_edges"] or c["boundary_edges"]:
                    tmpo.data = me
                    bpy.data.meshes.remove(light)
                else:
                    bpy.data.meshes.remove(me)
        bpy.ops.object.select_all(action="DESELECT")
        for t in temps:
            t.select_set(True)
        bpy.context.view_layer.objects.active = temps[0] if temps else None
        tmp = path + ".part"
        if fmt in ("glb", "gltf"):
            bpy.ops.export_scene.gltf(filepath=tmp, export_format="GLB" if fmt == "glb" else "GLTF_EMBEDDED",
                                      use_selection=True, export_apply=True, export_yup=True)
            if not os.path.exists(tmp) and os.path.exists(tmp + ".glb"):
                os.replace(tmp + ".glb", tmp)
            if not os.path.exists(tmp) and os.path.exists(tmp + ".gltf"):
                os.replace(tmp + ".gltf", tmp)
        elif fmt == "obj":
            bpy.ops.wm.obj_export(filepath=tmp, export_selected_objects=True, apply_modifiers=True,
                                  export_materials=False)
        elif fmt == "fbx":
            bpy.ops.export_scene.fbx(filepath=tmp, use_selection=True, apply_unit_scale=True)
        else:
            # Millimetres: what every slicer assumes an STL is in.
            bpy.ops.wm.stl_export(filepath=tmp, export_selected_objects=True, apply_modifiers=True,
                                  ascii_format=False, global_scale=1000.0)
        if not os.path.exists(tmp):
            raise Refused("the exporter wrote nothing")
        os.replace(tmp, path)
        checks = _mesh_checks(temps) if temps else {}
        return {"path": path, "bytes": os.path.getsize(path), "checks": checks, "objects": len(temps),
                **({"remeshed_voxel_mm": round(remeshed * 1000, 3)} if remeshed else {})}
    finally:
        for t in temps:
            me = t.data
            bpy.data.objects.remove(t, do_unlink=True)
            if me and me.users == 0:
                bpy.data.meshes.remove(me)
        if col and not col.objects:
            bpy.data.collections.remove(col)


def cmd_mesh_check(args):
    temps, col = _export_meshes(args.get("names") or [], union=bool(args.get("union", False)))
    try:
        return {"checks": _mesh_checks(temps) if temps else {}}
    finally:
        for t in temps:
            bpy.data.objects.remove(t, do_unlink=True)
        if col and not col.objects:
            bpy.data.collections.remove(col)


def _view3d_areas():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else []):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region:
                    yield win, area, region


def cmd_highlight(args):
    names = args.get("names") or []
    bpy.ops.object.select_all(action="DESELECT") if not bpy.app.background else None
    objs = [owned(n) for n in names]
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]
    framed = 0
    for win, area, region in _view3d_areas():
        with bpy.context.temp_override(window=win, area=area, region=region):
            if objs:
                bpy.ops.view3d.view_selected()
            else:
                bpy.ops.view3d.view_all()
            framed += 1
    return {"selected": [o.name for o in objs], "framed": framed}


def cmd_present(args):
    """Frame the model, solid shading with material colours, reference collection visible."""
    framed = 0
    parts = _part_objects([])
    for o in bpy.context.view_layer.objects:
        o.select_set(o in parts)
    for win, area, region in _view3d_areas():
        space = area.spaces.active
        space.shading.type = "SOLID"
        space.shading.color_type = "MATERIAL"
        with bpy.context.temp_override(window=win, area=area, region=region):
            if parts:
                bpy.ops.view3d.view_selected()
        framed += 1
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    return {"framed": framed, "parts": len(parts)}


def cmd_info(args):
    return {"version": list(bpy.app.version), "file": bpy.data.filepath, "objects": len(owned_objects()),
            "background": bool(bpy.app.background)}


def cmd_shutdown(args):
    STATE["stop"].set()
    return {}


COMMANDS = {
    "info": cmd_info, "new_project": cmd_new_project, "clear_project": cmd_clear_project,
    "apply": cmd_apply, "scene": cmd_scene, "snapshot": cmd_snapshot, "render": cmd_render,
    "save": cmd_save, "open": cmd_open, "export": cmd_export, "mesh_check": cmd_mesh_check,
    "highlight": cmd_highlight, "present": cmd_present, "shutdown": cmd_shutdown,
}


# ============================================================================ start
def _args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--socket", required=True)
    p.add_argument("--token-file", required=True)
    p.add_argument("--root", action="append", default=[])
    p.add_argument("--allowed-pid", action="append", type=int, default=[])
    p.add_argument("--owner", default="jarvis")
    p.add_argument("--max-render", type=int, default=1024)
    return p.parse_args(argv)


def _watch_owner() -> None:
    """When the JARVIS process that started us is gone, stop serving: a headless Blender exits,
    a window stays open for the user but no longer listens."""
    while not STATE["stop"].wait(2.0):
        alive = False
        for pid in STATE["allowed_pids"]:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                pass
            except PermissionError:
                alive = True
        if STATE["allowed_pids"] and not alive:
            STATE["stop"].set()
            return


def main() -> None:
    args = _args()
    version = tuple(bpy.app.version)
    if not (MIN_VERSION <= version < MAX_VERSION):
        print(f"JARVIS bridge: Blender {bpy.app.version_string} is not supported", file=sys.stderr)
        sys.exit(3)
    with open(args.token_file, "rb") as fh:
        STATE["token"] = fh.read().strip()
    os.unlink(args.token_file)                 # read once; nobody else gets to
    if len(STATE["token"]) < 32:
        sys.exit(4)
    STATE["allowed_pids"] = set(args.allowed_pid)
    STATE["roots"] = [os.path.realpath(r) for r in args.root]
    STATE["owner"] = args.owner
    STATE["max_render_px"] = max(64, min(args.max_render, 2048))
    _setup_scene()
    threading.Thread(target=serve, args=(args.socket,), daemon=True, name="jarvis-bridge").start()
    threading.Thread(target=_watch_owner, daemon=True, name="jarvis-owner").start()
    print("JARVIS bridge ready", flush=True)
    if bpy.app.background:
        while not STATE["stop"].is_set():
            run_one()
            time.sleep(0.01)
        run_one()
        sys.exit(0)

    def pump():
        run_one()
        if STATE["stop"].is_set():
            return None
        return 0.05

    bpy.app.timers.register(pump, persistent=True)


main()
