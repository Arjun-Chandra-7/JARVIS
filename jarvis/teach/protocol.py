"""The teaching overlay's command protocol — checked here before anything is sent.

What may be drawn is described once, in ``overlay/teach/protocol.json``, and read by both sides:
this module refuses a batch before it leaves Python, and ``overlay/teach/protocol.js`` refuses it
again in the Electron main process before the renderer sees it. Neither side trusts the other.

The shape of the rules, and why:

* Only listed operations and object types, only listed fields. An unknown key is an error, not
  something ignored — a field nobody validated is a field nobody thought about.
* No field carries code, markup, a URL or a file path. Text is text: it is rendered with
  ``textContent``, and anything that looks like a link or a path is refused outright.
* Everything is bounded: counts, sizes, durations, how far ahead a command may be scheduled, how
  old an anchor may be. A plan that wants a thousand objects or a ten-minute animation is wrong.

The model never writes any of this. It can at most choose a lesson and its words; the commands
are built by code (``jarvis.teach.lessons``) and then checked here anyway.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

SPEC_PATH = Path(__file__).resolve().parents[2] / "overlay" / "teach" / "protocol.json"
SPEC: dict = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
LIMITS: dict = SPEC["limits"]
OBJECT_TYPES = tuple(k for k in SPEC["objects"] if not k.startswith("_"))
OPS = tuple(SPEC["ops"])

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,%d}$" % LIMITS["max_id"])
_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,%d}(?:#[A-Za-z0-9_.:-]{1,%d})?$" % (LIMITS["max_id"], LIMITS["max_id"]))
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")
# A link, a script scheme, markup, or a path on this machine. Text on the overlay is none of these.
_UNSAFE_TEXT = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*:\s*//|\bjavascript\s*:|\bdata\s*:|\bfile\s*:|\bblob\s*:|\bvbscript\s*:|"
    r"<\s*/?\s*[a-z!?]|(?:^|[\s(\"'])(?:~|\.{1,2})?/(?:home|etc|usr|var|tmp|proc|root|dev|opt|srv|mnt|run)\b|"
    r"\\\\|[A-Za-z]:\\)")


class ProtocolError(ValueError):
    """A command the overlay must not receive. ``path`` says which field."""

    def __init__(self, path: str, why: str) -> None:
        super().__init__(f"{path}: {why}")
        self.path = path
        self.why = why


def now_ms() -> float:
    return time.time() * 1000.0


def _num(v: Any, path: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ProtocolError(path, "must be a finite number")
    return float(v)


def _range(spec: str) -> tuple[float, float]:
    lo, hi = spec.split("..")
    return float(lo), float(hi)


class Validator:
    """Interprets the type strings of protocol.json. Mirrors protocol.js line for line."""

    def __init__(self, spec: dict = SPEC, clock: Callable[[], float] = now_ms) -> None:
        self.spec = spec
        self.limits = spec["limits"]
        self.clock = clock
        self.colors = set(spec["colors"])

    # ------------------------------------------------------------------ scalar types
    def check(self, t: str, v: Any, path: str) -> Any:
        if t.startswith("?"):
            return None if v is None else self.check(t[1:], v, path)
        name, _, arg = t.partition(":")
        L = self.limits
        if name == "id":
            if not isinstance(v, str) or not _ID.match(v):
                raise ProtocolError(path, "must be a short identifier")
            return v
        if name == "ids":
            if not isinstance(v, list) or len(v) > 64:
                raise ProtocolError(path, "must be a list of at most 64 identifiers")
            return [self.check("id", x, f"{path}[{i}]") for i, x in enumerate(v)]
        if name == "ref":
            if not isinstance(v, str) or not _REF.match(v):
                raise ProtocolError(path, "must be an object id, optionally #term")
            return v
        if name == "refs":
            if not isinstance(v, list) or not 1 <= len(v) <= 32:
                raise ProtocolError(path, "must be 1–32 references")
            return [self.check("ref", x, f"{path}[{i}]") for i, x in enumerate(v)]
        if name == "bool":
            if not isinstance(v, bool):
                raise ProtocolError(path, "must be true or false")
            return v
        if name in ("int", "num"):
            n = _num(v, path)
            if name == "int" and n != int(n):
                raise ProtocolError(path, "must be a whole number")
            lo, hi = _range(arg)
            if not lo <= n <= hi:
                raise ProtocolError(path, f"must be between {arg}")
            return v
        if name == "unit":
            return self.check("num:0..1", v, path)
        if name == "ms":
            return self.check(f"num:0..{L['max_anim_ms']}", v, path)
        if name == "delay":
            return self.check(f"num:0..{L['max_delay_ms']}", v, path)
        if name == "coord":
            return self.check(f"num:{-L['max_coord']}..{L['max_coord']}", v, path)
        if name == "size":
            n = _num(v, path)
            if not 0 < n <= L["max_size"]:
                raise ProtocolError(path, "must be a positive size within the screen")
            return v
        if name == "epoch":
            n = _num(v, path)
            now = self.clock()
            if not now - 86_400_000 <= n <= now + L["max_schedule_ahead_ms"]:
                raise ProtocolError(path, "is not a time close to now")
            return v
        if name == "point":
            if not isinstance(v, list) or len(v) != 2:
                raise ProtocolError(path, "must be [x, y]")
            return [self.check("coord", v[0], path + "[0]"), self.check("coord", v[1], path + "[1]")]
        if name == "points":
            if not isinstance(v, list) or not 2 <= len(v) <= L["max_points"]:
                raise ProtocolError(path, f"must be 2–{L['max_points']} points")
            return [self.check("point", p, f"{path}[{i}]") for i, p in enumerate(v)]
        if name == "tri":
            if not isinstance(v, list) or len(v) != 3:
                raise ProtocolError(path, "a triangle has three points")
            pts = [self.check("point", p, f"{path}[{i}]") for i, p in enumerate(v)]
            (ax, ay), (bx, by), (cx, cy) = pts
            if abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2 < 4:
                raise ProtocolError(path, "is not a triangle (its points are in a line)")
            return pts
        if name == "rect":
            if not isinstance(v, dict) or set(v) != {"x", "y", "w", "h"}:
                raise ProtocolError(path, "must be {x, y, w, h}")
            self.check("coord", v["x"], path + ".x")
            self.check("coord", v["y"], path + ".y")
            self.check("size", v["w"], path + ".w")
            self.check("size", v["h"], path + ".h")
            return v
        if name == "monitor":
            if v in ("primary", "pointer"):
                return v
            return self.check(f"int:0..{L['max_monitor']}", v, path)
        if name == "text":
            if not isinstance(v, str) or len(v) > L["max_text"]:
                raise ProtocolError(path, f"must be text of at most {L['max_text']} characters")
            if _CONTROL.search(v):
                raise ProtocolError(path, "contains control characters")
            if _UNSAFE_TEXT.search(v):
                raise ProtocolError(path, "looks like a link, markup or a file path")
            return v
        if name == "color":
            if not isinstance(v, str) or not (v in self.colors or _HEX.match(v)):
                raise ProtocolError(path, "must be a palette name or #rrggbb")
            return v
        if name == "enum":
            if str(v) not in arg.split(",") or isinstance(v, bool):
                raise ProtocolError(path, f"must be one of {arg}")
            return v
        if name == "otype":
            if v not in OBJECT_TYPES:
                raise ProtocolError(path, "is not a known object type")
            return v
        if name in ("style", "anim", "theme", "transform", "anchor", "term"):
            return self.struct(self.spec[name], v, path, name)
        if name == "terms":
            if not isinstance(v, list) or not 1 <= len(v) <= L["max_terms"]:
                raise ProtocolError(path, f"must be 1–{L['max_terms']} terms")
            return [self.check("term", x, f"{path}[{i}]") for i, x in enumerate(v)]
        if name == "object":
            return self.object(v, path)
        if name == "objects":
            if not isinstance(v, list) or len(v) > L["max_objects"]:
                raise ProtocolError(path, f"must be at most {L['max_objects']} objects")
            return [self.object(o, f"{path}[{i}]") for i, o in enumerate(v)]
        if name == "patch":
            return self.patch(v, path)
        if name == "commands":
            if not isinstance(v, list) or not 1 <= len(v) <= L["max_batch_commands"]:
                raise ProtocolError(path, f"must be 1–{L['max_batch_commands']} commands")
            return [self.command(c, f"{path}[{i}]") for i, c in enumerate(v)]
        raise ProtocolError(path, f"unknown type {t!r} in the protocol")

    def struct(self, fields: dict, v: Any, path: str, what: str) -> dict:
        if not isinstance(v, dict):
            raise ProtocolError(path, f"{what} must be an object")
        extra = set(v) - set(fields)
        if extra:
            raise ProtocolError(path, f"unknown field {sorted(extra)[0]!r}")
        for key, t in fields.items():
            if not t.startswith("?") and key not in v:
                raise ProtocolError(f"{path}.{key}", "is required")
            if key in v:
                self.check(t, v[key], f"{path}.{key}")
        if what == "anchor":
            age = self.clock() - float(v["observed_at"])
            if age > self.limits["max_anchor_age_ms"]:
                raise ProtocolError(path + ".observed_at", "the anchor is stale")
        return v

    def object(self, v: Any, path: str) -> dict:
        if not isinstance(v, dict) or v.get("type") not in OBJECT_TYPES:
            raise ProtocolError(path + ".type", "is not a known object type")
        fields = {**self.spec["objects"]["_common"], **self.spec["objects"][v["type"]]}
        return self.struct(fields, v, path, "object")

    def patch(self, v: Any, path: str, otype: Optional[str] = None) -> dict:
        if not isinstance(v, dict) or not v:
            raise ProtocolError(path, "must be a non-empty object")
        allowed = set(self.spec["patchable"])
        for key, value in v.items():
            if key not in allowed:
                raise ProtocolError(f"{path}.{key}", "cannot be changed")
            # Without the object's type the value must be valid for some type that has the key;
            # the renderer re-checks the merged object against its real type.
            candidates = ([self.spec["objects"][otype].get(key) or self.spec["objects"]["_common"].get(key)]
                          if otype else
                          [f[key] for f in self.spec["objects"].values() if key in f])
            errors = []
            for t in candidates:
                if t is None:
                    continue
                try:
                    self.check(t.lstrip("?"), value, f"{path}.{key}")
                    break
                except ProtocolError as e:
                    errors.append(e)
            else:
                raise errors[0] if errors else ProtocolError(f"{path}.{key}", "cannot be changed")
        return v

    def command(self, v: Any, path: str) -> dict:
        if not isinstance(v, dict) or not isinstance(v.get("op"), str):
            raise ProtocolError(path + ".op", "is missing")
        op = v["op"]
        if op not in self.spec["ops"]:
            raise ProtocolError(path + ".op", f"unknown command {op[:40]!r}")
        fields = {"op": "enum:" + op, **self.spec["ops"][op]}
        self.struct(fields, v, path, "command")
        if op in ("stroke.draw",) and v["object"]["type"] not in ("stroke", "highlighter"):
            raise ProtocolError(path + ".object.type", "stroke.draw draws strokes only")
        return v

    def envelope(self, v: Any) -> dict:
        raw = json.dumps(v, ensure_ascii=False, allow_nan=False)
        if len(raw.encode("utf-8")) > self.limits["max_payload_bytes"]:
            raise ProtocolError("batch", "is too large")
        return self.struct(self.spec["envelope"], v, "batch", "envelope")


VALIDATOR = Validator()


def validate(batch: dict) -> dict:
    """The batch, unchanged, or ProtocolError naming the first thing wrong with it."""
    return VALIDATOR.envelope(batch)
