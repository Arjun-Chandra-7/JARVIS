"""The overlay as a whiteboard for the image generator, and drawings that never get stuck."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from jarvis import draw_command
from jarvis.teach import assistant
from jarvis.teach import runner as runner_mod
from jarvis.teach.bus import Overlay, _NullTransport, use
from jarvis.teach.protocol import ProtocolError, validate
from jarvis.teach.runner import KEEP_HOLD_S, LessonRunner
from jarvis.teach.scene import add, create, obj

ROOT = Path(__file__).resolve().parent.parent
AREA = {"index": 0, "scale": 1.0, "w": 1920, "h": 1080, "work": {"x": 0, "y": 28, "w": 1920, "h": 1052}}


@pytest.fixture()
def ov(monkeypatch):
    o = use(Overlay(_NullTransport()))
    monkeypatch.setattr(o, "work_area", lambda monitor="primary": AREA)
    r = LessonRunner(o)
    old = runner_mod.RUNNER
    runner_mod.RUNNER = r
    yield o
    r._cancel_cleanup()
    runner_mod.RUNNER = old


# --------------------------------------------------------------------------- never stuck
def test_hold_is_part_of_the_protocol_and_bounded():
    ok = {"v": 1, "lesson": "l", "gen": 1, "seq": 1, "cmds": [{"op": "scene.create", "scene": "s", "monitor": 0, "hold_s": 60}]}
    validate(ok)
    for bad in (0, 4, 99999, "60"):
        with pytest.raises(ProtocolError):
            validate({**ok, "cmds": [{"op": "scene.update", "hold_s": bad}]})


def test_requested_drawings_and_leave_it_ask_for_the_long_hold(ov):
    r = runner_mod.RUNNER
    r.draw([create("d", 0), add(obj("c", "circle", cx=100, cy=100, r=20))])
    assert {"op": "scene.update", "hold_s": KEEP_HOLD_S} in ov.transport.commands()
    ov.transport.sent.clear()
    r.leave()
    assert ov.transport.commands() == [{"op": "scene.update", "hold_s": KEEP_HOLD_S}]


def test_the_overlay_expires_scenes_nobody_updates():
    js = (ROOT / "overlay/teach/main-teach.js").read_text(encoding="utf-8")
    assert "function armHold()" in js and 'type: "expired"' in js
    assert "if (pen || !shown) return;" in js                     # never mid-pen


def test_clear_works_on_a_drawing_this_process_did_not_make(ov):
    ov.visible = True                                              # the overlay says it shows something
    assert assistant.wants("clear the screen")
    rep = asyncio.run(assistant.handle("clear the screen"))
    assert rep.text == "Cleared."
    assert ("teach_control", {"action": "clear"}) in ov.transport.sent
    ov.visible = False
    assert not assistant.wants("clear it")                         # nothing there: not ours


# --------------------------------------------------------------------------- the generator as a source
def test_generate_and_draw_is_one_request():
    assert draw_command.make_and_draw("Generate an image of a dragon and draw it on the whiteboard") == "a dragon"
    assert draw_command.make_and_draw("create a picture of a red fox then draw it") == "a red fox"
    assert draw_command.make_and_draw("make an image of the Taj Mahal and recreate it on screen") == "the Taj Mahal"
    assert draw_command.make_and_draw("draw me a cat") is None
    assert draw_command.make_and_draw("generate an image of a fox") is None


def _picture(tmp_path, name="p.png"):
    import cv2
    img = np.full((512, 512, 3), 255, np.uint8)
    cv2.circle(img, (256, 256), 150, (0, 0, 0), 6)
    cv2.line(img, (120, 400), (400, 120), (0, 0, 0), 5)
    p = tmp_path / name
    cv2.imwrite(str(p), img)
    return p


def test_picture_order_generated_then_generator_then_reference(tmp_path, monkeypatch):
    from jarvis import context
    from jarvis.vision import imagine

    made = _picture(tmp_path, "made.png")
    monkeypatch.setattr(context, "last_picture", lambda: str(made))
    pic, src = asyncio.run(draw_command._picture_for("it", None))
    assert pic == made and "just generated" in src

    asked = []

    class Made:
        path, seconds = _picture(tmp_path, "gen.png"), 4.4

    monkeypatch.setattr(imagine, "ready", lambda: True)
    monkeypatch.setattr(imagine, "generate", lambda prompt, **k: asked.append(prompt) or Made)
    monkeypatch.setattr(context, "note_picture", lambda p: None)
    pic, src = asyncio.run(draw_command._picture_for("a cat", None))
    assert pic == Made.path and "generated" in src
    assert "line drawing" in asked[0] and asked[0].startswith("a cat")

    monkeypatch.setattr(imagine, "ready", lambda: False)
    ref = type("R", (), {"find": staticmethod(lambda s: _picture(tmp_path, "ref.png"))})
    pic, src = asyncio.run(draw_command._picture_for("a cat", ref))
    assert src == "a reference picture"


def test_drawing_on_the_overlay_traces_outlines_within_limits(tmp_path, monkeypatch, ov):
    from jarvis import context
    from jarvis.vision import imagine, strokes

    made = _picture(tmp_path)
    monkeypatch.setattr(context, "last_picture", lambda: str(made))
    monkeypatch.setattr(imagine, "ready", lambda: False)
    reply = asyncio.run(draw_command._draw_on_overlay("it", None, strokes, title="a circle"))
    assert reply.startswith("Drew a circle on the screen")
    cmds = ov.transport.commands()
    drawn = [c for c in cmds if c["op"] == "stroke.draw"]
    assert drawn and len(drawn) <= draw_command.OVERLAY_STROKES
    for b in ov.transport.batches():
        validate(b)                                                 # every batch within the protocol's limits


def test_outline_only_tracing_skips_hatching(tmp_path):
    from jarvis.vision import strokes
    p = _picture(tmp_path)
    with_hatch = strokes.from_image(p, 20000)
    outline = strokes.from_image(p, 20000, hatch=False)
    assert outline is not None and len(outline.strokes) <= len(with_hatch.strokes)
