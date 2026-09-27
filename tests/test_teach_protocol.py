"""The teaching overlay's protocol: what may be drawn, checked on both sides of the boundary.

Every case runs through the Python validator and, when Node is installed, through the Electron
main process's validator too — the same spec, two implementations, one verdict. A difference
between them would be a hole: Python refusing what the overlay accepts is harmless, the other
way round is the whole problem.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from jarvis.teach import protocol
from jarvis.teach.protocol import ProtocolError, Validator

ROOT = Path(__file__).resolve().parent.parent
NOW = time.time() * 1000
# Both validators read the same fixed clock, so "fresh" means fresh relative to these cases.
validate = Validator(clock=lambda: NOW).envelope


def batch(*cmds, **extra):
    return {"v": 1, "lesson": "l1", "gen": 1, "seq": 1, "cmds": list(cmds), **extra}


def tri(id_="t1", pts=((100, 500), (100, 200), (500, 500))):
    return {"op": "shape.add", "object": {"id": id_, "type": "triangle", "points": [list(p) for p in pts],
                                          "style": {"color": "primary", "width": 3, "glow": 0.5},
                                          "anim": {"kind": "draw", "ms": 900}}}


def anchor(**over):
    a = {"id": "a1", "source": "region", "bounds": {"x": 10, "y": 10, "w": 300, "h": 200},
         "confidence": 0.9, "observed_at": NOW, "ttl_ms": 3000, "monitor": 0, "scale": 1.0,
         "tracking": "follow"}
    a.update(over)
    return {"op": "anchor.attach", "anchor": a}


GOOD = {
    "triangle": batch(tri()),
    "scene": batch({"op": "scene.create", "scene": "s1", "monitor": "primary",
                    "theme": {"palette": "jarvis", "glow": 0.6, "reduced_motion": False},
                    "objects": [{"id": "p", "type": "panel", "x": 1200, "y": 120, "w": 600, "h": 700,
                                 "title": "Pythagoras theorem"}]}),
    "equation": batch({"op": "shape.add", "object": {
        "id": "eq", "type": "equation", "x": 300, "y": 700, "size": 44,
        "terms": [{"id": "a", "text": "a", "sup": "2"}, {"id": "p1", "text": " + "},
                  {"id": "b", "text": "b", "sup": "2"}, {"id": "eq", "text": " = "},
                  {"id": "c", "text": "c", "sup": "2", "color": "confirm"}]}}),
    "hindi text": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 5, "y": 5,
                                                       "text": "कर्ण (c) — सबसे लंबी भुजा"}}),
    "ratio text is not a path": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 5,
                                                                     "y": 5, "text": "a/b and 3/4"}}),
    "highlight term": batch({"op": "highlight.show", "target": "eq#a", "color": "attention", "pulse": True}),
    "anchor": batch(anchor()),
    "patch": batch({"op": "shape.update", "id": "t1", "patch": {"style": {"color": "confirm"}, "visible": True}}),
    "timeline": batch({"op": "timeline.pause"}, {"op": "timeline.resume", "timeline": "step-2"},
                      {"op": "timeline.cancel", "timeline": "step-2"}),
    "stroke": batch({"op": "stroke.draw", "object": {"id": "s", "type": "highlighter",
                                                      "points": [[0, 0], [10, 10], [20, 5]]}}),
    "scheduled": batch(tri(), at=NOW + 200, sent=NOW),
    "node and edge": batch(
        {"op": "shape.add", "object": {"id": "n1", "type": "node", "x": 10, "y": 10, "w": 200, "h": 70,
                                       "label": "Vector database", "icon": "db"}},
        {"op": "shape.add", "object": {"id": "e1", "type": "edge", "from": "n1", "to": "n2", "flow": True}}),
}


def _bad(mutate):
    b = batch(tri())
    mutate(b)
    return b


BAD = {
    "unknown op": batch({"op": "eval", "code": "require('child_process')"}),
    "script in text": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 0, "y": 0,
                                                           "text": "<script>alert(1)</script>"}}),
    "img onerror": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 0, "y": 0,
                                                        "text": "<img src=x onerror=alert(1)>"}}),
    "javascript url": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 0, "y": 0,
                                                           "text": "javascript:alert(1)"}}),
    "http url": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 0, "y": 0,
                                                     "text": "see https://evil.example/x"}}),
    "local path": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 0, "y": 0,
                                                       "text": "open /home/me/.ssh/id_rsa"}}),
    "file url": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 0, "y": 0,
                                                     "text": "file:///etc/passwd"}}),
    "extra field": _bad(lambda b: b["cmds"][0]["object"].__setitem__("onclick", "x()")),
    "extra envelope field": batch(tri(), html="<b>"),
    "unknown object type": batch({"op": "shape.add", "object": {"id": "x", "type": "iframe", "src": "a"}}),
    "negative size": batch({"op": "shape.add", "object": {"id": "r", "type": "rect", "x": 0, "y": 0,
                                                          "w": -5, "h": 10}}),
    "zero size": batch({"op": "shape.add", "object": {"id": "r", "type": "rect", "x": 0, "y": 0,
                                                      "w": 0, "h": 10}}),
    "huge coordinate": batch({"op": "shape.add", "object": {"id": "c", "type": "circle", "cx": 1e9,
                                                            "cy": 0, "r": 5}}),
    "nan": _bad(lambda b: b["cmds"][0]["object"]["points"][0].__setitem__(0, "NaN")),
    "degenerate triangle": batch(tri(pts=((0, 0), (10, 10), (20, 20)))),
    "unbounded animation": _bad(lambda b: b["cmds"][0]["object"]["anim"].__setitem__("ms", 600000)),
    "too many commands": batch(*[tri(f"t{i}") for i in range(200)]),
    "too many points": batch({"op": "stroke.draw", "object": {"id": "s", "type": "stroke",
                                                              "points": [[i % 50, i % 30] for i in range(5000)]}}),
    "stroke.draw of a rect": batch({"op": "stroke.draw", "object": {"id": "r", "type": "rect", "x": 0,
                                                                     "y": 0, "w": 5, "h": 5}}),
    "invalid monitor": batch({"op": "scene.create", "scene": "s", "monitor": 99}),
    "monitor as text": batch({"op": "scene.create", "scene": "s", "monitor": "HDMI-1"}),
    "stale anchor": batch(anchor(observed_at=NOW - 60_000)),
    "anchor in the future": batch(anchor(observed_at=NOW + 3_600_000)),
    "bad id": batch({"op": "shape.remove", "id": "x'); drop"}),
    "bad colour": _bad(lambda b: b["cmds"][0]["object"]["style"].__setitem__("color", "url(#x)")),
    "patch unknown field": batch({"op": "shape.update", "id": "t1", "patch": {"innerHTML": "<b>"}}),
    "patch bad value": batch({"op": "shape.update", "id": "t1", "patch": {"text": "http://x.y"}}),
    "schedule far ahead": batch(tri(), at=NOW + 3_600_000),
    "wrong version": _bad(lambda b: b.__setitem__("v", 2)),
    "boolean as number": batch({"op": "shape.add", "object": {"id": "c", "type": "circle", "cx": True,
                                                              "cy": 0, "r": 5}}),
    "control characters": batch({"op": "shape.add", "object": {"id": "t", "type": "text", "x": 0,
                                                               "y": 0, "text": "a‮b"}}),
    "no commands": batch(),
}


@pytest.mark.parametrize("name", sorted(GOOD))
def test_valid_batches_pass(name):
    validate(copy.deepcopy(GOOD[name]))


@pytest.mark.parametrize("name", sorted(BAD))
def test_unsafe_or_impossible_batches_are_refused(name):
    with pytest.raises(ProtocolError):
        validate(copy.deepcopy(BAD[name]))


def test_every_object_type_in_the_spec_is_drawable():
    """The renderer draws each type the protocol admits — a type added to the spec and not to the
    renderer would pass validation and then draw nothing."""
    renderer = (ROOT / "overlay" / "teach" / "renderer.js").read_text(encoding="utf-8")
    for t in protocol.OBJECT_TYPES:
        assert f'"{t}"' in renderer, t
    for op in protocol.OPS:
        assert f'"{op}"' in renderer, op


def test_payload_size_is_bounded():
    """Each command within limits, the batch as a whole not: 150 strokes of 4000 points."""
    points = [[i % 500, i % 300] for i in range(4000)]
    huge = batch(*[{"op": "stroke.draw", "object": {"id": f"s{i}", "type": "stroke", "points": points}}
                   for i in range(150)])
    with pytest.raises(ProtocolError, match="too large"):
        validate(huge)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_overlay_validator_agrees_with_this_one():
    cases = {**{f"good:{k}": v for k, v in GOOD.items()}, **{f"bad:{k}": v for k, v in BAD.items()}}
    script = r"""
const spec = require(process.argv[1] + "/overlay/teach/protocol.json");
const { make } = require(process.argv[1] + "/overlay/teach/protocol.js");
const v = make(spec, () => Number(process.argv[2]));
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  const out = {};
  for (const [name, b] of Object.entries(JSON.parse(input))) {
    try { v.envelope(b); out[name] = "ok"; } catch (e) { out[name] = "refused: " + e.message; }
  }
  process.stdout.write(JSON.stringify(out));
});
"""
    done = subprocess.run(["node", "-e", script, str(ROOT), str(NOW)], input=json.dumps(cases), text=True,
                          capture_output=True, timeout=30)
    assert done.returncode == 0, done.stderr
    verdicts = json.loads(done.stdout)
    for name, verdict in verdicts.items():
        if name.startswith("good:"):
            assert verdict == "ok", (name, verdict)
        else:
            assert verdict.startswith("refused"), (name, verdict)
