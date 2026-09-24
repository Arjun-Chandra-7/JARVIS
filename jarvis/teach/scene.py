"""Building overlay commands, and remembering what they drew.

The overlay is told what to draw; it is never asked what it has drawn. So this side keeps its own
copy of the scene, applying each command the same way the renderer does. That copy is what makes
"undo", "go back one step" and "erase that" possible: a step's starting picture is a snapshot of
it, and restoring one is a single ``scene.create`` with the objects as they were, drawn without
animation.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

Cmd = dict


# --------------------------------------------------------------------------- builders
def obj(id: str, type: str, *, group: str = "jarvis", style: Optional[dict] = None, anim: Optional[dict] = None,
        anchor: Optional[str] = None, z: Optional[int] = None, visible: Optional[bool] = None, **fields) -> dict:
    o: dict[str, Any] = {"id": id, "type": type, **fields}
    if group != "jarvis":
        o["group"] = group
    if style:
        o["style"] = style
    if anim:
        o["anim"] = anim
    if anchor:
        o["anchor"] = anchor
    if z is not None:
        o["z"] = z
    if visible is not None:
        o["visible"] = visible
    return o


def anim(kind: str = "draw", ms: float = 700, delay: float = 0, ease: str = "out", timeline: str = "") -> dict:
    a: dict[str, Any] = {"kind": kind, "ms": ms}
    if delay:
        a["delay"] = delay
    if ease != "out":
        a["ease"] = ease
    if timeline:
        a["timeline"] = timeline
    return a


def add(o: dict) -> Cmd:
    return {"op": "shape.add", "object": o}


def update(id: str, **patch) -> Cmd:
    return {"op": "shape.update", "id": id, "patch": patch}


def remove(id: str, fade_ms: float = 180) -> Cmd:
    return {"op": "shape.remove", "id": id, "fade_ms": fade_ms}


def highlight(target: str, color: str = "attention", pulse: bool = True, ms: float = 0) -> Cmd:
    c: Cmd = {"op": "highlight.show", "target": target, "color": color, "pulse": pulse}
    if ms:
        c["ms"] = ms
    return c


def unhighlight(target: Optional[str] = None) -> Cmd:
    return {"op": "highlight.hide", **({"target": target} if target else {})}


def focus(*targets: str, dim: float = 0.75) -> Cmd:
    return {"op": "focus.show", "targets": list(targets), "dim": dim}


def unfocus() -> Cmd:
    return {"op": "focus.hide"}


def create(scene: str, monitor: Any = "primary", theme: Optional[dict] = None,
           objects: Optional[list] = None, region: Optional[dict] = None) -> Cmd:
    c: Cmd = {"op": "scene.create", "scene": scene, "monitor": monitor}
    if theme:
        c["theme"] = theme
    if objects is not None:
        c["objects"] = objects
    if region:
        c["region"] = region
    return c


def clear(group: Optional[str] = None, keep: Optional[list] = None) -> Cmd:
    c: Cmd = {"op": "scene.clear"}
    if group:
        c["group"] = group
    if keep:
        c["keep_groups"] = keep
    return c


def still(o: dict) -> dict:
    """The object as it ends up, without its entrance animation."""
    o = copy.deepcopy(o)
    o.pop("anim", None)
    return o


# --------------------------------------------------------------------------- the mirror
@dataclass
class SceneModel:
    """What the overlay is showing, as far as the commands sent to it say."""

    scene: str = ""
    monitor: Any = "primary"
    theme: dict = field(default_factory=dict)
    objects: dict[str, dict] = field(default_factory=dict)    # insertion-ordered
    transform: dict = field(default_factory=lambda: {"scale": 1.0, "dx": 0.0, "dy": 0.0})
    last_ref: Optional[str] = None           # the object most recently drawn or spoken about
    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)

    def apply(self, cmds: Iterable[Cmd]) -> None:
        for c in cmds:
            op = c["op"]
            if op == "scene.create":
                self.scene = c["scene"]
                self.monitor = c.get("monitor", "primary")
                self.theme = dict(c.get("theme") or {})
                self.transform = {"scale": 1.0, "dx": 0.0, "dy": 0.0}
                keep = {k: v for k, v in self.objects.items() if v.get("group") == "manual"}
                self.objects = {**keep, **{o["id"]: still(o) for o in c.get("objects") or []}}
            elif op == "scene.update":
                if c.get("theme"):
                    self.theme.update(c["theme"])
                if c.get("transform"):
                    self.transform.update({k: v for k, v in c["transform"].items() if k in ("scale", "dx", "dy", "origin")})
            elif op == "scene.clear":
                group, keep = c.get("group"), set(c.get("keep_groups") or [])
                self.objects = {k: v for k, v in self.objects.items()
                                if not (v.get("group", "jarvis") == group if group
                                        else v.get("group", "jarvis") not in keep)}
            elif op in ("shape.add", "stroke.draw"):
                o = c["object"]
                self.objects.pop(o["id"], None)
                self.objects[o["id"]] = still(o)
                self.last_ref = o["id"]
            elif op == "shape.update" and c["id"] in self.objects:
                cur = self.objects[c["id"]]
                patch = copy.deepcopy(c["patch"])
                patch.pop("anim", None)
                if "style" in patch:
                    patch["style"] = {**cur.get("style", {}), **patch["style"]}
                cur.update(patch)
                self.last_ref = c["id"]
            elif op == "shape.remove":
                self.objects.pop(c["id"], None)
            elif op == "group.remove":
                self.objects = {k: v for k, v in self.objects.items() if v.get("group", "jarvis") != c["id"]}
            elif op == "highlight.show":
                self.last_ref = c["target"].split("#")[0]

    # --------------------------------------------------------------- snapshots
    def snapshot(self) -> dict:
        return {"scene": self.scene, "monitor": self.monitor, "theme": dict(self.theme),
                "objects": copy.deepcopy(self.objects), "transform": dict(self.transform)}

    def restore_cmds(self, snap: dict) -> list[Cmd]:
        """Commands that put the overlay back exactly as in ``snap``, instantly."""
        objects = [o for o in snap["objects"].values() if o.get("group") != "manual"]
        cmds: list[Cmd] = [create(snap["scene"] or "restored", snap["monitor"], snap["theme"] or None, objects)]
        t = snap.get("transform") or {}
        if t and (t.get("scale", 1) != 1 or t.get("dx") or t.get("dy")):
            cmds.append({"op": "scene.update", "transform": {**{k: t[k] for k in ("scale", "dx", "dy", "origin") if k in t}, "ms": 0}})
        return cmds

    # --------------------------------------------------------------- undo / redo
    def checkpoint(self) -> None:
        """Call before a change the person may want to undo."""
        self.undo_stack.append(self.snapshot())
        del self.undo_stack[:-30]
        self.redo_stack.clear()

    def undo(self) -> Optional[list[Cmd]]:
        if not self.undo_stack:
            return None
        self.redo_stack.append(self.snapshot())
        snap = self.undo_stack.pop()
        cmds = self.restore_cmds(snap)
        self.apply(cmds)
        return cmds

    def redo(self) -> Optional[list[Cmd]]:
        if not self.redo_stack:
            return None
        self.undo_stack.append(self.snapshot())
        snap = self.redo_stack.pop()
        cmds = self.restore_cmds(snap)
        self.apply(cmds)
        return cmds

    # --------------------------------------------------------------- lookups
    def jarvis_objects(self) -> dict[str, dict]:
        return {k: v for k, v in self.objects.items() if v.get("group", "jarvis") != "manual"}

    def empty(self) -> bool:
        return not self.objects

    def bounds_of(self, id: str) -> Optional[tuple[float, float, float, float]]:
        o = self.objects.get(id)
        if not o:
            return None
        if {"x", "y", "w", "h"} <= set(o):
            return (o["x"], o["y"], o["w"], o["h"])
        if o["type"] in ("circle",):
            return (o["cx"] - o["r"], o["cy"] - o["r"], 2 * o["r"], 2 * o["r"])
        if o["type"] == "ellipse":
            return (o["cx"] - o["rx"], o["cy"] - o["ry"], 2 * o["rx"], 2 * o["ry"])
        pts = o.get("points") or [p for p in (o.get("from"), o.get("to"), o.get("at")) if p]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return (min(xs), min(ys), max(xs) - min(xs) or 1, max(ys) - min(ys) or 1)
        if "x" in o and "y" in o:
            return (o["x"], o["y"] - 20, 120, 28)
        return None
