"""Voice edits → typed operations on the parametric model.

``parse(text, model)`` reads one request ("make the base 15 percent wider", "lock the drum
shells; only edit the hardware", "add two evenly spaced holes") into an ``EditOperation`` with
resolved target parts — or into a ``Clarify`` when the words fit several parts, or when the
natural target is locked. ``apply(model, op)`` performs it on a copy of the model and says which
parts changed, were added or removed, and what their dimensions must be afterwards, so the
studio can verify Blender and roll back when it does not match.

Nothing here touches Blender. Nothing here runs text from an image.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from . import calibration
from .types import EditOperation, Evidence, Material, ModelPart, StudioModel

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "a": 1, "an": 1, "another": 1, "single": 1, "pair": 2, "couple": 2, "twenty": 20,
              "fifteen": 15, "fifty": 50, "thirty": 30, "forty": 40, "first": 1, "second": 2, "third": 3,
              "fourth": 4, "fifth": 5}
_STOP = {"the", "a", "an", "my", "our", "this", "that", "these", "those", "of", "on", "in", "to", "it", "its",
         "only", "please", "part", "parts", "piece", "one", "jarvis", "model", "some", "all", "just", "both"}
_SIDE = {"left", "right", "front", "back", "rear", "top", "bottom", "upper", "lower"}

_AXIS_ADJ = {"wider": ("x", 1), "narrower": ("x", -1), "broader": ("x", 1), "longer": ("x", 1),
             "shorter": ("z", -1), "taller": ("z", 1), "higher": ("z", 1), "lower": ("z", -1),
             "deeper": ("y", 1), "shallower": ("y", -1), "thicker": ("y", 1), "thinner": ("y", -1),
             "bigger": ("xyz", 1), "larger": ("xyz", 1), "smaller": ("xyz", -1)}
_DIM_WORD = {"width": "x", "wide": "x", "length": "x", "long": "x", "depth": "y", "deep": "y", "thickness": "y",
             "thick": "y", "height": "z", "tall": "z", "high": "z", "diameter": "d", "radius": "r"}


@dataclass
class Clarify:
    question: str
    candidates: list
    pending: EditOperation


@dataclass
class Outcome:
    model: StudioModel
    changed: list = field(default_factory=list)
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    expect: dict = field(default_factory=dict)          # part → expected [x,y,z] mm (source geometry)
    message: str = ""
    geometry: bool = True                               # False: materials/locks only


class Refused(ValueError):
    pass


def _num(word: str) -> Optional[float]:
    word = word.lower().strip()
    if word in _NUM_WORDS:
        return float(_NUM_WORDS[word])
    try:
        return float(word)
    except ValueError:
        return None


def _stem(w: str) -> str:
    w = w.lower()
    for suf in ("es", "s"):
        if len(w) > 3 and w.endswith(suf) and not w.endswith("ss"):
            return w[: -len(suf)] if suf == "s" or w[:-2].endswith(("sh", "ch", "x")) else w[:-1]
    return w


# ============================================================================ targets
def resolve(model: StudioModel, phrase: str, *, roles=("part", "cutter")) -> list[ModelPart]:
    """Parts the phrase names. Several may match; sides ("left") pick among them."""
    words = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if w not in _STOP]
    if not words or set(words) <= {"object", "whole", "everything", "thing", "entire"}:
        return [p for p in model.parts if p.role == "part"]
    sides = [w for w in words if w in _SIDE]
    core = [_stem(w) for w in words if w not in _SIDE]
    scored = []
    for p in model.parts:
        if p.role not in roles:
            continue
        names = {_stem(t) for t in re.findall(r"[a-z0-9]+", p.name.lower())} | {_stem(a) for a in p.aliases}
        if p.material:
            names |= {_stem(t) for t in re.findall(r"[a-z]+", p.material.lower())}
        hit = sum(1 for w in core if w in names)
        if core and hit:
            scored.append((hit, p))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    parts = [p for s, p in scored if s == best]
    # "the holes" names the group; "hole 2" names one
    nums = [w for w in words if w.isdigit()]
    if nums:
        parts = [p for p in parts if p.name.split()[-1] in nums] or parts
    if sides and len(parts) > 1:
        side = sides[0]
        key = {"left": lambda p: p.location[0], "right": lambda p: -p.location[0],
               "front": lambda p: p.location[1], "back": lambda p: -p.location[1], "rear": lambda p: -p.location[1],
               "top": lambda p: -p.location[2], "upper": lambda p: -p.location[2],
               "bottom": lambda p: p.location[2], "lower": lambda p: p.location[2]}[side]
        parts = [min(parts, key=key)]
    return parts


def _one_or_ask(model, phrase, op: EditOperation, *, group_ok: bool = True):
    parts = resolve(model, phrase)
    if not parts:
        names = ", ".join(p.name for p in model.parts if p.role == "part")
        raise Refused(f"I can't find a part called {phrase.strip() or 'that'}. The parts are: {names}.")
    plural = bool(re.search(r"\b\w+s\b", phrase.split()[-1] if phrase.split() else "")) and not phrase.strip().endswith("ss")
    if len(parts) > 1 and not (group_ok and plural):
        names = [p.name for p in parts]
        return Clarify(f"Which one — {', '.join(names[:-1])} or {names[-1]}?", names, op)
    op.target = [p.name for p in parts]
    return op


# ============================================================================ parsing
_LEN_RE = r"(?P<len>\d+(?:\.\d+)?\s*(?:mm|cm|m|millimet\w*|centimet\w*|met(?:er|re)s?|inch(?:es)?|in|ft|feet|\")?)"


def parse(text: str, model: StudioModel):
    """EditOperation, Clarify, or None (not an edit). Raises Refused with a speakable reason."""
    t = re.sub(r"(?i)^(?:hey\s+)?jarvis[,\s]+", "", text.strip()).rstrip(".!? ").lower()
    t = re.sub(r"\bper\s?cent\b", "percent", t)

    # --- history
    if re.fullmatch(r"(?:undo|revert)(?: (?:that|it|the last (?:change|edit)|last change|the change))?", t):
        return EditOperation(target=[], operation="undo")
    if re.fullmatch(r"redo(?: (?:that|it|the last (?:change|edit)))?", t):
        return EditOperation(target=[], operation="redo")
    m = re.search(r"(?:go back to|restore|revert to|return to|open|switch to) version (\w+)", t)
    if m:
        n = _num(m.group(1))
        if n is None:
            raise Refused("Which version number?")
        return EditOperation(target=[], operation="restore", amount=n)
    m = re.fullmatch(r"(?:undo|remove|take away|get rid of) the (?P<what>.+)", t)
    if m and re.search(r"\b(?:holes?|copy|copies|bevel|rounding|duplicate|change to|edit to)\b", m.group("what")):
        return EditOperation(target=[], operation="undo_matching", extra={"what": m.group("what")})

    # --- locks
    m = re.match(r"(?:lock|freeze|protect)(?: the)? (?P<t>[^;,.]+?)(?:\s*(?:[;,.]|and)\s*(?P<rest>.*))?$", t)
    if m:
        op = EditOperation(target=[], operation="lock")
        res = _one_or_ask(model, m.group("t"), op)
        if isinstance(res, Clarify):
            return res
        rest = m.group("rest") or ""
        only = re.search(r"only (?:edit|change|touch|modify) (?:the )?(?P<o>.+)", rest)
        if only:
            keep = {p.name for p in resolve(model, only.group("o"))}
            op.extra["only"] = sorted(keep)
            op.target = sorted({p.name for p in model.parts if p.role == "part" and p.name not in keep} | set(op.target))
        return op
    m = re.match(r"(?:unlock|unfreeze)(?: the)? (?P<t>.+)", t)
    if m:
        op = EditOperation(target=[], operation="unlock")
        return _one_or_ask(model, m.group("t"), op)

    # --- exports and views
    if re.search(r"game[\s-]?ready|for (?:a |the )?game|low[\s-]?poly copy", t):
        return EditOperation(target=[], operation="export", extra={"profile": "game"})
    if re.search(r"(?:export|save|make).*(?:3d print|printing|printable|print it|\bstl\b)", t):
        return EditOperation(target=[], operation="export", extra={"profile": "print"})
    m = re.search(r"export(?: it)?(?: as| to)? (?:an? |the )?(glb|gltf|obj|fbx|stl|web)", t)
    if m:
        prof = {"glb": "web", "gltf": "web", "web": "web", "obj": "obj", "fbx": "fbx", "stl": "print"}[m.group(1)]
        return EditOperation(target=[], operation="export", extra={"profile": prof})
    if re.search(r"before and after|compare (?:it )?with (?:the )?(?:previous|last) version", t):
        return EditOperation(target=[], operation="compare")
    if re.search(r"match the reference(?: silhouette)? more closely|refine (?:it|the model|the shape)|improve the match", t):
        return EditOperation(target=[], operation="refine")
    if re.search(r"(?:keep|leave) the front .*(?:rebuild|redo|remake) the back", t):
        return EditOperation(target=[], operation="rebuild_back")

    # --- materials
    adjectives = re.findall(r"\b(darker|lighter|brighter|shinier|glossier|less reflective|more reflective|matte|"
                            r"matt|rougher|smoother|less shiny|more metallic|less metallic)\b", t)
    m = re.match(r"make the (?P<t>.+?) (?:" + "|".join(["darker", "lighter", "brighter", "shinier", "glossier",
                                                        "less reflective", "more reflective", "matte", "matt",
                                                        "rougher", "smoother", "less shiny", "more metallic",
                                                        "less metallic"]) + r")\b", t)
    if m and adjectives:
        return EditOperation(target=[m.group("t")], operation="material", extra={"adjectives": adjectives})

    # --- geometry
    m = re.match(r"(?:make|scale) the (?P<t>.+?) (?P<n>[\w.]+) ?(?:%|percent) (?P<adj>" + "|".join(_AXIS_ADJ) + r")\b", t)
    if m:
        pct = _num(m.group("n"))
        if pct is None:
            raise Refused("By how much?")
        axis, sign = _AXIS_ADJ[m.group("adj")]
        op = EditOperation(target=[], operation="scale", amount=1 + sign * pct / 100.0, relative=True, unit="%",
                           axis=axis, expected=f"{m.group('adj')} by {pct:g}%")
        return _one_or_ask(model, m.group("t"), op)
    m = re.match(r"(?:increase|decrease|set|change|make|resize|reduce)(?: the)? (?P<t>.+?)(?:'s)? "
                 r"(?P<dim>diameter|width|height|depth|thickness|radius|length)(?: to| of| at)? " + _LEN_RE, t)
    if not m:
        m = re.match(r"make the (?P<t>.+?) " + _LEN_RE + r" (?:in )?(?P<dim>wide|tall|high|deep|thick|long|diameter|across)", t)
    if m:
        got = calibration.parse_length(m.group("len"))
        if not got:
            raise Refused("What size?")
        value, unit = got
        if unit is None:
            raise Refused(f"{value:g} what — millimetres, centimetres or inches?")
        dim = {"across": "d"}.get(m.group("dim"), _DIM_WORD[m.group("dim")])
        op = EditOperation(target=[], operation="set_dimension", amount=value, relative=False, unit="mm", axis=dim,
                           expected=f"{m.group('dim')} {value:g} mm")
        return _one_or_ask(model, m.group("t"), op)
    m = re.match(r"move the (?P<t>.+?)(?: (?P<amt>slightly|a (?:little|bit|touch)|" + _LEN_RE[1:-1].replace("?P<len>", "")
                 + r"))? (?P<dir>inwards?|outwards?|left|right|up|down|forwards?|backwards?|back|closer together|apart)$", t)
    if m:
        op = EditOperation(target=[], operation="move", axis=m.group("dir"), relative=True,
                           extra={"amount": (m.group("amt") or "slightly")})
        return _one_or_ask(model, m.group("t"), op)
    m = re.match(r"add (?P<n>\w+) (?:evenly |equally )?(?:spaced )?(?:(?P<size>[\d.]+) ?mm )?holes?"
                 r"(?: (?:to|in|into|through|on) (?:the )?(?P<t>.+))?$", t)
    if m:
        n = _num(m.group("n"))
        if not n or n > 24:
            raise Refused("How many holes?")
        op = EditOperation(target=[], operation="add_holes", amount=n, unit="mm",
                           extra={"diameter": float(m.group("size")) if m.group("size") else None})
        if m.group("t"):
            return _one_or_ask(model, m.group("t"), op)
        host = _default_host(model)
        if host.is_locked:
            others = [p.name for p in model.parts if p.role == "part" and not p.is_locked and p.geometry in ("box", "cylinder")]
            q = f"The {host.name.lower()} is locked. Should the holes go in " + \
                (" or ".join(f"the {o.lower()}" for o in others[:2]) + ", or should I unlock it?" if others
                 else "it anyway once you unlock it?")
            return Clarify(q, others, op)
        op.target = [host.name]
        return op
    m = re.match(r"round (?:off )?(?P<only>only )?the (?P<which>top )?(?:corners|edges)(?: of (?:the )?(?P<t>.+))?", t)
    if m:
        op = EditOperation(target=[], operation="bevel", extra={"top_only": bool(m.group("which"))})
        if m.group("t"):
            return _one_or_ask(model, m.group("t"), op)
        host = _default_host(model)
        if host.is_locked:
            raise Refused(f"The {host.name.lower()} is locked, so I left its corners alone.")
        op.target = [host.name]
        return op
    m = re.match(r"add (?:another|one more|a second)(?: identical)? (?P<t>.+?)(?: (?:on|to) the (?P<side>left|right|front|back))?$", t)
    if m:
        op = EditOperation(target=[], operation="duplicate", axis=m.group("side") or "right")
        return _one_or_ask(model, m.group("t"), op, group_ok=False)
    m = re.match(r"(?:the )?(?P<t>.+?) should be deeper|make the (?P<t2>.+?) (?:cut |engraving )?deeper", t)
    if m and re.search(r"engrav|hole|cut|groove|recess|slot", t):
        op = EditOperation(target=[], operation="deepen", amount=1.5)
        parts = [p for p in model.parts if p.role == "cutter"]
        if not parts:
            raise Refused("There's no engraving or hole in this model yet to make deeper.")
        op.target = [p.name for p in parts]
        return op
    m = re.match(r"make the (?P<t>.+?) (?:more|less) (?P<q>curved|rounded|round)", t)
    if m:
        op = EditOperation(target=[], operation="curvature", amount=1 if "more" in t else -1)
        return _one_or_ask(model, m.group("t"), op)
    return None


def _default_host(model: StudioModel) -> ModelPart:
    flat = [p for p in model.parts if p.role == "part" and p.geometry in ("box", "cylinder")] or \
           [p for p in model.parts if p.role == "part"]
    return max(flat, key=lambda p: p.dimensions()[0] * p.dimensions()[1])


# ============================================================================ applying
def _check_unlocked(parts: list[ModelPart]) -> None:
    locked = [p.name for p in parts if p.is_locked]
    if locked:
        raise Refused(f"{', '.join(locked)} {'is' if len(locked) == 1 else 'are'} locked, so I didn't change "
                      f"{'it' if len(locked) == 1 else 'them'}. Say \"unlock {locked[0].lower()}\" first.")


def source_dims(p: ModelPart) -> list:
    return [round(float(v), 3) for v in p.dimensions()]


def _dependents(model: StudioModel, names: set) -> set:
    """Parts whose modifiers point at a changed part (mirror about it) must be rebuilt with it."""
    out = set()
    for p in model.parts:
        for mod in p.modifiers:
            if mod.get("about") in names:
                out.add(p.name)
    return out


def _amount_mm(model: StudioModel, words: str) -> float:
    got = calibration.parse_length(words) if re.search(r"\d", words) else None
    if got and got[1]:
        return got[0]
    size = max(max(p.dimensions()) for p in model.parts if p.role == "part")
    return max(1.0, round(size * 0.03, 2))           # "slightly": 3% of the object, at least 1 mm


def apply(model: StudioModel, op: EditOperation) -> Outcome:
    """Perform ``op`` on a copy. Raises Refused for locked parts or impossible requests."""
    m = copy.deepcopy(model)
    parts = [m.part(n) for n in op.target if m.part(n)]
    kind = op.operation
    out = Outcome(m)

    if kind in ("lock", "unlock"):
        for p in parts:
            p.locked = ["all"] if kind == "lock" else []
        out.geometry = False
        out.message = (f"{'Locked' if kind == 'lock' else 'Unlocked'} {_names(parts)}."
                       + (f" Only {_names([m.part(n) for n in op.extra['only']])} can be edited now."
                          if op.extra.get("only") else ""))
        return out

    if kind == "material":
        phrase = op.target[0] if op.target else ""
        mats = _materials_for(m, phrase)
        if not mats:
            raise Refused(f"I can't find a {phrase} material on this model.")
        users = [p for p in m.parts if p.material in {x.name for x in mats}]
        _check_unlocked(users)
        for mat in mats:
            for adj in op.extra["adjectives"]:
                if adj == "darker":
                    mat.color = [round(c * 0.65, 4) for c in mat.color]
                elif adj in ("lighter", "brighter"):
                    mat.color = [round(min(1.0, c * 1.35 + 0.05), 4) for c in mat.color]
                elif adj in ("less reflective", "matte", "matt", "rougher", "less shiny"):
                    mat.roughness = round(min(1.0, mat.roughness + 0.3), 3)
                elif adj in ("more reflective", "shinier", "glossier", "smoother"):
                    mat.roughness = round(max(0.02, mat.roughness - 0.25), 3)
                elif adj == "more metallic":
                    mat.metallic = round(min(1.0, mat.metallic + 0.4), 3)
                elif adj == "less metallic":
                    mat.metallic = round(max(0.0, mat.metallic - 0.4), 3)
            mat.evidence = Evidence.CONSTRAINED
        out.changed = [p.name for p in users]
        out.geometry = False
        out.expect = {"materials": {x.name: {"color": x.color, "roughness": x.roughness, "metallic": x.metallic}
                                    for x in mats}}
        out.message = f"Made the {phrase} {' and '.join(op.extra['adjectives'])}."
        return out

    if kind == "scale":
        _check_unlocked(parts)
        for p in parts:
            _scale(p, op.axis, op.amount)
        out.changed = [p.name for p in parts]
        out.message = f"Made the {_names(parts)} {op.expected}."
    elif kind == "set_dimension":
        _check_unlocked(parts)
        for p in parts:
            _set_dim(p, op.axis, op.amount)
        out.changed = [p.name for p in parts]
        out.message = f"Set the {_names(parts)} {op.expected}."
    elif kind == "move":
        _check_unlocked(parts)
        locked_kids = [m.part(c) for p in parts for c in p.children if m.part(c) and m.part(c).is_locked]
        if locked_kids:
            raise Refused(f"Moving the {_names(parts)} would move {_names(locked_kids)}, which is locked.")
        step = _amount_mm(m, op.extra.get("amount", ""))
        centre = _centre(m)
        for p in parts:
            d = _direction(op.axis, p, centre)
            p.location = [round(p.location[i] + d[i] * step, 3) for i in range(3)]
            for c in p.children:
                kid = m.part(c)
                if kid:
                    kid.location = [round(kid.location[i] + d[i] * step, 3) for i in range(3)]
        out.changed = [p.name for p in parts] + [c for p in parts for c in p.children]
        out.message = f"Moved the {_names(parts)} {step:g} mm {op.axis}."
    elif kind == "add_holes":
        host = parts[0]
        _check_unlocked([host])
        added = _add_holes(m, host, int(op.amount), op.extra.get("diameter"))
        out.added = added
        out.changed = [host.name]
        d = m.part(added[0]).params["radius"] * 2
        out.message = (f"Added {len(added)} evenly spaced {d:g} mm holes through the {host.name.lower()}. "
                       "Their size is my choice — tell me the diameter if it matters.")
    elif kind == "bevel":
        _check_unlocked(parts)
        for p in parts:
            dims = p.dimensions()
            w = round(min(dims) * 0.12, 2)
            p.modifiers = [x for x in p.modifiers if x["type"] != "bevel"]
            p.modifiers.append({"type": "bevel", "width": w, "segments": 4,
                                **({"only": "top"} if op.extra.get("top_only") else {})})
        out.changed = [p.name for p in parts]
        out.message = f"Rounded {'only the top' if op.extra.get('top_only') else 'the'} edges of the {_names(parts)}."
    elif kind == "duplicate":
        src = parts[0]
        dup = copy.deepcopy(src)
        n = 2
        while m.part(f"{src.name} copy {n}" if n > 2 else f"{src.name} copy"):
            n += 1
        dup.name = f"{src.name} copy {n}" if n > 2 else f"{src.name} copy"
        dup.locked, dup.children = [], []
        dims = src.dimensions()
        gap = {"left": (-1, 0), "right": (1, 0), "front": (0, -1), "back": (0, 1)}[op.axis]
        dup.location = [round(src.location[0] + gap[0] * dims[0] * 1.15, 3),
                        round(src.location[1] + gap[1] * dims[1] * 1.15, 3), src.location[2]]
        dup.evidence = Evidence.INVENTED
        dup.aliases = list(src.aliases) + ["copy", "duplicate"]
        m.parts.append(dup)
        if dup.parent and m.part(dup.parent):
            m.part(dup.parent).children.append(dup.name)
        out.added = [dup.name]
        out.message = f"Added an identical copy of the {src.name.lower()} on the {op.axis}."
    elif kind == "deepen":
        cutters = [p for p in parts if p.role == "cutter"]
        hosts = {p.params.get("host") for p in cutters}
        _check_unlocked([m.part(h) for h in hosts if m.part(h)])
        for c in cutters:
            c.params["height"] = round(c.params["height"] * op.amount, 3)
        out.changed = [c.name for c in cutters] + sorted(h for h in hosts if h)
        out.message = f"Made the {_names(cutters)} {int((op.amount - 1) * 100)}% deeper."
    elif kind == "curvature":
        _check_unlocked(parts)
        for p in parts:
            if p.geometry == "curve_extrude":
                p.params["smooth"] = op.amount > 0
            else:
                subs = [x for x in p.modifiers if x["type"] == "subdivision"]
                level = (subs[0]["levels"] if subs else 0) + int(op.amount)
                p.modifiers = [x for x in p.modifiers if x["type"] != "subdivision"]
                if level > 0:
                    p.modifiers.append({"type": "subdivision", "levels": min(level, 3)})
        out.changed = [p.name for p in parts]
        out.message = f"Made the {_names(parts)} {'more' if op.amount > 0 else 'less'} curved."
    else:
        raise Refused("I don't know how to make that change yet.")
    changed = set(out.changed)
    out.changed = sorted(changed | _dependents(m, changed))
    out.expect = {n: source_dims(m.part(n)) for n in out.changed + out.added if m.part(n)}
    return out


def _names(parts) -> str:
    names = [p.name.lower() for p in parts if p]
    if not names:
        return "nothing"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _materials_for(m: StudioModel, phrase: str) -> list[Material]:
    words = set(re.findall(r"[a-z]+", phrase.lower())) - _STOP
    if words & {"metal", "metallic", "steel", "chrome", "aluminium", "aluminum", "iron", "brass", "hardware"}:
        return [x for x in m.materials if x.metallic >= 0.5 or re.search(r"(?i)metal|steel|alumin|chrome|brass", x.name)]
    by_part = {p.material for p in resolve(m, phrase) if p.material}
    by_name = [x for x in m.materials if words & set(re.findall(r"[a-z]+", x.name.lower()))]
    return [x for x in m.materials if x.name in by_part] or by_name


def _scale(p: ModelPart, axis: str, f: float) -> None:
    pr = p.params
    if p.geometry == "box":
        for a in axis:
            pr[{"x": "x", "y": "y", "z": "z"}[a]] = round(pr[a] * f, 3)
        if "z" in axis:
            p.location[2] = round(p.location[2] + (pr["z"] - pr["z"] / f) / 2, 3)   # grow upward, stay on the ground
    elif p.geometry in ("cylinder", "cutter"):
        if set(axis) & {"x", "y"}:
            pr["radius"] = round(pr["radius"] * f, 3)
        if "z" in axis:
            old = pr["height"]
            pr["height"] = round(old * f, 3)
            p.location[2] = round(p.location[2] + (pr["height"] - old) / 2, 3)
    elif p.geometry == "revolve":
        pr["profile"] = [[round(r * (f if set(axis) & {"x", "y"} else 1), 4), round(z * (f if "z" in axis else 1), 4)]
                         for r, z in pr["profile"]]
    elif p.geometry == "curve_extrude":
        sx = f if set(axis) & {"x"} else 1.0
        sy = f if "z" in axis else 1.0                  # stood-up profile: its y is the world height
        x0, x1, y0, y1 = p.curve_extent()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2         # about the layer's own centre, so it stays put

        def s(pt):
            return [round(cx + (pt[0] - cx) * sx, 4), round(cy + (pt[1] - cy) * sy, 4)]
        pr["polygons"] = [{"outer": [s(q) for q in poly["outer"]], "holes": [[s(q) for q in h] for h in poly["holes"]]}
                          for poly in pr["polygons"]]
        if "y" in axis:
            pr["depth"] = round(pr["depth"] * f, 3)
    else:
        raise Refused("That part is a scanned mesh; I can only move or copy it, not resize one axis.")


def _set_dim(p: ModelPart, axis: str, value: float) -> None:
    dims = p.dimensions()
    if axis in ("d", "r"):
        if p.geometry not in ("cylinder", "cutter", "sphere", "revolve"):
            raise Refused(f"The {p.name.lower()} isn't round, so it has no diameter.")
        cur = dims[0]
        target = value * (2 if axis == "r" else 1)
        _scale(p, "xy", target / cur)
        return
    idx = "xyz".index(axis)
    if dims[idx] <= 0:
        raise Refused("That part has no size on that axis.")
    _scale(p, axis, value / dims[idx])


def _centre(m: StudioModel) -> list:
    ps = [p for p in m.parts if p.role == "part"]
    return [sum(p.location[i] for p in ps) / len(ps) for i in range(3)]


def _direction(word: str, p: ModelPart, centre: list) -> list:
    word = word.rstrip("s") if word not in ("apart",) else word
    fixed = {"left": [-1, 0, 0], "right": [1, 0, 0], "up": [0, 0, 1], "down": [0, 0, -1], "forward": [0, -1, 0],
             "back": [0, 1, 0], "backward": [0, 1, 0]}
    if word in fixed:
        return fixed[word]
    v = [centre[0] - p.location[0], centre[1] - p.location[1], 0.0]
    n = math.hypot(v[0], v[1])
    if n < 1e-6:
        return [0.0, 0.0, 0.0]
    sign = 1 if word in ("inward", "closer together") else -1
    return [sign * v[0] / n, sign * v[1] / n, 0.0]


def _add_holes(m: StudioModel, host: ModelPart, n: int, diameter: Optional[float]) -> list[str]:
    """Evenly spaced through-holes along the host's long axis, clear of the parts standing on it."""
    dims = host.dimensions()
    L, W, H = dims
    d = diameter or round(max(2.0, min(L, W) * 0.1), 1)
    long_x = L >= W
    span = L if long_x else W
    obstacles = []
    h_lo, h_hi = host.location[2] - H / 2, host.location[2] + H / 2
    for p in m.parts:
        if p is host or p.role != "part":
            continue
        pd = p.dimensions()
        p_lo, p_hi = p.location[2] - pd[2] / 2, p.location[2] + pd[2] / 2
        if min(h_hi, p_hi) - max(h_lo, p_lo) <= 0.5 and not (p.parent == host.name and p_lo >= h_hi - 0.5):
            continue                   # below the host, or merely touching it: a through-hole won't reach it
        mirrored = [1] + ([-1] if any(x["type"] == "mirror" for x in p.modifiers) else [])
        for s in mirrored:
            cx = host.location[0] + s * (p.location[0] - host.location[0])
            obstacles.append((cx - pd[0] / 2, cx + pd[0] / 2, p.location[1] - pd[1] / 2, p.location[1] + pd[1] / 2))
    lines = [0.0, (W if long_x else L) / 3, -(W if long_x else L) / 3]
    best, best_hits = None, None
    for line in lines:
        pts = []
        for i in range(n):
            a = -span / 2 + span * (i + 1) / (n + 1)
            x, y = (host.location[0] + a, host.location[1] + line) if long_x else (host.location[0] + line, host.location[1] + a)
            pts.append((x, y))
        hits = sum(1 for x, y in pts for (x0, x1, y0, y1) in obstacles
                   if x0 - d <= x <= x1 + d and y0 - d <= y <= y1 + d)
        if best_hits is None or hits < best_hits:
            best, best_hits = pts, hits
        if hits == 0:
            break
    if best_hits:
        raise Refused(f"There isn't room for {n} holes of {d:g} mm on the {host.name.lower()} without cutting the parts on it.")
    if host.geometry in ("cylinder", "cutter"):
        R = host.params["radius"]
        if any(math.hypot(x - host.location[0], y - host.location[1]) + d / 2 > R - 0.5 for x, y in best):
            raise Refused(f"{n} holes of {d:g} mm don't fit inside the {host.name.lower()}.")
    names = []
    k = 1 + sum(1 for p in m.parts if p.role == "cutter" and p.name.startswith("Hole"))
    for x, y in best:
        name = f"Hole {k}"
        k += 1
        m.parts.append(ModelPart(name=name, geometry="cutter", role="cutter",
                                 params={"radius": d / 2, "height": round(H + 2.0, 3), "host": host.name,
                                         "operation": "difference", "segments": 48},
                                 location=[round(x, 3), round(y, 3), host.location[2]], evidence=Evidence.INVENTED,
                                 confidence=1.0, aliases=["hole", "holes"]))
        names.append(name)
    return names
