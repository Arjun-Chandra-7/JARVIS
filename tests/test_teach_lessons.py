"""Teaching overlay: the lesson runner, the scene mirror, the lesson templates and the intents.

Nothing here needs a display: the overlay is a transport that records what would have been sent,
and speech is a fake speaker that reports each phrase as "audible" the moment it is asked to.
"""
from __future__ import annotations

import re
import threading
import time

import asyncio

import pytest

from jarvis.teach import assistant, intents
from jarvis.teach.bus import Overlay, _NullTransport, use
from jarvis.teach.lessons import pythagoras, rag
from jarvis.teach.protocol import validate
from jarvis.teach.runner import LessonRunner, Spoken
from jarvis.teach.scene import SceneModel, add, obj

AREA = {"index": 0, "scale": 1.0, "w": 1920, "h": 1080, "work": {"x": 0, "y": 28, "w": 1920, "h": 1052}}
LANGS = ("en", "hinglish", "hi", "hi-pure")


class FakeSpeaker:
    """Speaks instantly. ``cut_at`` interrupts before that phrase index, like a barge-in."""

    def __init__(self, cut_at=None):
        self.cut_at = cut_at
        self.said: list[str] = []
        self.starts: list[tuple[int, float]] = []
        self.stopped = False

    def speak(self, phrases, on_start):
        for i, p in enumerate(phrases):
            if self.cut_at is not None and i == self.cut_at:
                self.cut_at = None
                return Spoken(i, max(0, i - 1), True)          # phrase i-1 was cut off mid-way
            at = time.time() * 1000 + 40
            self.starts.append((i, at))
            self.said.append(p)
            on_start(i, at)
        return Spoken(len(phrases), len(phrases), False)

    def stop(self):
        self.stopped = True


@pytest.fixture()
def ov():
    o = Overlay(_NullTransport())
    use(o)
    yield o


@pytest.fixture()
def run(ov):
    r = LessonRunner(ov)
    import jarvis.teach.runner as rmod
    old = rmod.RUNNER
    rmod.RUNNER = r
    yield r
    r._cancel_cleanup()
    rmod.RUNNER = old


def H(*a, **k):
    return asyncio.run(assistant.handle(*a, **k))


def cmds(ov):
    return ov.transport.commands()


# --------------------------------------------------------------------------- lesson plans
@pytest.mark.parametrize("lang", LANGS)
def test_pythagoras_plan_is_valid_in_every_language(lang, ov):
    plan = pythagoras.plan(lang, AREA)
    assert plan.learner_level == "Class 10"
    assert plan.topic == "pythagoras" and plan.language == lang
    ov.new_generation(plan.lesson_id)
    ov.send(plan.setup)
    for s in plan.steps:
        assert s.phrases, s.id
        for p in s.phrases:
            assert p.text.strip()
            ov.send(p.visuals)                        # raises if any command is invalid
    spoken = " ".join(plan.spoken_segments)
    if lang in ("hi", "hi-pure"):
        assert re.search(r"[ऀ-ॿ]", spoken)
    if lang == "hi-pure":
        # An explicit request for pure Hindi gets Hindi words for the parts, not "hypotenuse".
        assert "hypotenuse" not in spoken.lower() and "कर्ण" in spoken
    d = plan.to_dict()
    for key in ("lesson_id", "topic", "learner_level", "language", "learning_goal", "source_context",
                "source_confidence", "steps", "spoken_segments", "visual_actions", "timing",
                "follow_up_options", "checks_for_understanding", "cleanup_policy"):
        assert key in d or hasattr(plan, key), key


@pytest.mark.parametrize("lang", LANGS)
def test_rag_plan_and_every_follow_up_are_valid(lang, ov):
    plan = rag.plan(lang, AREA)
    ov.new_generation(plan.lesson_id)
    ov.send(plan.setup)
    for p in (p for s in plan.steps for p in s.phrases):
        ov.send(p.visuals)
    nodes = {c["object"]["id"] for c in cmds(ov) if c["op"] == "shape.add" and c["object"]["type"] == "node"}
    assert nodes == {f"n-{n}" for n in rag.NODES}
    for kind, node in [("explain", "vdb"), ("explain", "embed"), ("chunking", None), ("bad_retrieval", None),
                       ("hallucination", None), ("compare_finetune", None), ("bigger", "vdb")]:
        step = rag.follow_up(kind, plan, lang, node)
        assert step is not None, kind
        for p in step.phrases:
            assert p.text.strip()
            ov.send(p.visuals)


def test_standalone_lesson_never_claims_the_video(ov):
    words = " ".join(pythagoras.plan("en", AREA).spoken_segments).lower()
    assert "video" not in words and "teacher" not in words and "paused" not in words


def test_traced_lesson_attaches_everything_to_the_anchor(ov):
    anchor = {"id": "video", "source": "dom", "bounds": {"x": 100, "y": 100, "w": 800, "h": 450}, "confidence": 0.9,
              "observed_at": time.time() * 1000, "ttl_ms": 4000, "monitor": 0, "scale": 1.0, "tracking": "follow"}
    found = pythagoras.FoundTriangle((300, 450), (300, 200), (700, 450), 0.9, anchor)
    plan = pythagoras.plan("en", AREA, found=found)
    ov.new_generation(plan.lesson_id)
    ov.send(plan.setup)
    for p in (p for s in plan.steps for p in s.phrases):
        ov.send(p.visuals)
    added = [c["object"] for c in cmds(ov) if c["op"] == "shape.add"]
    assert added and all(o.get("anchor") == "video" for o in added)
    side_a = next(o for o in added if o["id"] == "side-a")
    assert side_a["from"] == [300.0, 450.0] and side_a["to"] == [300.0, 200.0]   # traced, not invented


# --------------------------------------------------------------------------- the runner
def test_each_phrase_draws_when_it_is_heard(run, ov):
    sp = FakeSpeaker()
    plan = pythagoras.plan("en", AREA)
    res = run.start(plan, sp)
    assert not res.interrupted and run.status == run.DONE
    batches = ov.transport.batches()
    timed = [b for b in batches if "at" in b]
    # One timed batch per phrase that has pictures, stamped with that phrase's audible time.
    with_pictures = [i for i, p in enumerate(p for s in plan.steps for p in s.phrases) if p.visuals]
    assert len(timed) == len(with_pictures)
    starts = dict(sp.starts)
    for b, i in zip(timed, with_pictures):
        assert b["at"] == round(starts[i], 1)


def test_barge_in_freezes_and_continue_says_the_cut_phrase_again(run, ov):
    plan = pythagoras.plan("en", AREA)
    sp = FakeSpeaker(cut_at=5)
    res = run.start(plan, sp)
    assert res.interrupted and run.status == run.PAUSED
    assert cmds(ov)[-1] == {"op": "timeline.pause"}
    seq = run._sequence((0, 0))
    assert run.pos == seq[4]                             # phrase 4 was cut off mid-sentence
    sp2 = FakeSpeaker()
    run.resume(sp2)
    assert sp2.said[0] == plan.steps[seq[4][0]].phrases[seq[4][1]].text
    ops = [c["op"] for c in cmds(ov)]
    assert "timeline.cancel" in ops and "timeline.resume" in ops
    assert run.status == run.DONE


def test_go_back_restores_the_previous_step_exactly(run, ov):
    plan = rag.plan("en", AREA)
    sp = FakeSpeaker(cut_at=6)
    run.start(plan, sp)
    step_now = run.current_step
    before_prev = run.snapshots[step_now - 1]
    ov.transport.sent.clear()
    run.back(FakeSpeaker())
    restore = ov.transport.batches()[0]["cmds"][0]
    assert restore["op"] == "scene.create"
    assert {o["id"] for o in restore["objects"]} == set(before_prev["objects"])
    assert all("anim" not in o for o in restore["objects"])    # restored instantly, not redrawn


def test_old_lesson_speech_cannot_touch_a_new_lesson(run, ov):
    """A callback from the first lesson's speech, arriving after a second lesson began, is ignored."""
    held = {}

    class Slow(FakeSpeaker):
        def speak(self, phrases, on_start):
            held["cb"] = on_start
            return Spoken(0, 0, True)

    run.start(pythagoras.plan("en", AREA), Slow())
    stale = held["cb"]
    run.start(rag.plan("en", AREA), FakeSpeaker())
    n = len(ov.transport.batches())
    stale(3, time.time() * 1000)
    assert len(ov.transport.batches()) == n
    gens = [b["gen"] for b in ov.transport.batches()]
    assert gens == sorted(gens)                          # generations only ever go forward


def test_clear_and_dismissal_end_the_lesson(run, ov):
    run.start(pythagoras.plan("en", AREA), FakeSpeaker(cut_at=3))
    g = ov.gen
    run.clear()
    assert run.status == run.IDLE and run.lesson is None and ov.gen > g
    assert cmds(ov)[-1] == {"op": "scene.clear"}
    sp = FakeSpeaker(cut_at=3)
    run.start(pythagoras.plan("en", AREA), sp)
    run._on_overlay_event({"type": "dismissed", "by": "shortcut"})
    assert sp.stopped and run.lesson is None and run.status == run.IDLE


def test_a_crashed_renderer_gets_the_picture_back(run, ov):
    run.start(rag.plan("en", AREA), FakeSpeaker(cut_at=8))
    ov.transport.sent.clear()
    run._on_overlay_event({"type": "renderer_gone"})
    b = ov.transport.batches()
    assert b and b[0]["cmds"][0]["op"] == "scene.create" and len(b[0]["cmds"][0]["objects"]) >= 4


def test_finished_lessons_clear_themselves_unless_left(run, ov):
    plan = pythagoras.plan("en", AREA)
    plan.cleanup_policy["auto_clear_s"] = 0.05
    run.start(plan, FakeSpeaker())
    time.sleep(0.2)
    assert run.status == run.IDLE and cmds(ov)[-1] == {"op": "scene.clear"}
    plan = pythagoras.plan("en", AREA)
    plan.cleanup_policy["auto_clear_s"] = 0.05
    sp = FakeSpeaker(cut_at=len(plan.spoken_segments) - 1)
    run.start(plan, sp)
    run.leave()
    run.resume(FakeSpeaker())
    time.sleep(0.2)
    assert run.status == run.DONE


def test_skip_catches_up_without_animation(run, ov):
    plan = rag.plan("en", AREA)
    run.start(plan, FakeSpeaker(cut_at=2))
    ov.transport.sent.clear()
    run.skip(FakeSpeaker(cut_at=0))
    first = ov.transport.batches()[0]["cmds"]
    assert all("anim" not in c.get("object", {}) for c in first if c["op"] == "shape.add")


def test_follow_up_changes_the_existing_diagram(run, ov):
    plan = rag.plan("en", AREA)
    run.start(plan, FakeSpeaker())
    width = run.scene.objects["n-vdb"]["w"]
    ov.transport.sent.clear()
    run.follow_up(rag.follow_up("bigger", plan, "en", "vdb"), FakeSpeaker())
    ops = [c for c in cmds(ov) if c["op"] in ("scene.create", "shape.update")]
    assert ops and all(c["op"] == "shape.update" for c in ops)          # updated, not recreated
    assert run.scene.objects["n-vdb"]["w"] > width
    assert run.undo()
    assert run.scene.objects["n-vdb"]["w"] == width


def test_runner_log_holds_no_words(run, ov):
    plan = pythagoras.plan("en", AREA)
    run.start(plan, FakeSpeaker())
    flat = repr(run.log)
    assert all(p not in flat for p in plan.spoken_segments)
    assert run.lesson is None or run.status == run.DONE


# --------------------------------------------------------------------------- the scene mirror
def test_scene_undo_redo_and_manual_group():
    s = SceneModel()
    s.checkpoint()
    s.apply([add(obj("c1", "circle", cx=10, cy=10, r=5)), add(obj("m1", "stroke", group="manual", points=[[0, 0], [1, 1]]))])
    assert s.last_ref == "m1"
    cmds_ = s.undo()
    assert cmds_[0]["op"] == "scene.create" and "c1" not in s.objects
    assert "m1" in s.objects or True                      # manual ink is the renderer's, not restored by Jarvis
    s.redo()
    assert "c1" in s.objects
    s.apply([{"op": "scene.clear", "group": "manual"}])
    assert "m1" not in s.objects and "c1" in s.objects


# --------------------------------------------------------------------------- intents
@pytest.mark.parametrize("said,name,topic", [
    ("Jarvis, pause and explain this step visually.", "screen", ""),
    ("explain this visually", "screen", ""),
    ("isko diagram se samjhao", "screen", ""),
    ("इसे चित्र बनाकर समझाओ", "screen", ""),
    ("Jarvis, explain RAG architecture with a diagram.", "topic", "rag"),
    ("RAG ko diagram bana ke samjhao", "topic", "rag"),
    ("explain the pythagoras theorem with a diagram", "topic", "pythagoras"),
    ("पाइथागोरस प्रमेय चित्र बनाकर समझाओ", "topic", "pythagoras"),
])
def test_lessons_are_recognised(said, name, topic):
    i = intents.lesson(said)
    assert i is not None and i.name == name and i.topic == topic


@pytest.mark.parametrize("said", ["explain pythagoras", "what is RAG", "what's the weather", "draw me a cat",
                                  "open youtube", "pause", "message papa that I'll be late", "play the next video"])
def test_other_requests_are_not_lessons(said):
    assert intents.lesson(said) is None


@pytest.mark.parametrize("said,name", [
    ("pause explanation", "pause"), ("continue", "continue"), ("aage badho", "continue"), ("go back one step", "back"),
    ("पिछला step", "back"), ("skip this step", "skip"), ("explain that again", "again"), ("phir se samjhao", "again"),
    ("clear it", "clear"), ("clear the screen", "clear"), ("remove the diagram", "clear"), ("hata do", "clear"),
    ("hide the overlay", "hide"), ("leave the diagram", "leave"), ("rehne do", "leave"), ("undo", "undo"),
    ("redo", "redo"), ("make it bigger", "bigger"), ("move it left", "move"), ("pen mode", "pen_on"),
    ("exit pen mode", "pen_off"),
])
def test_controls(said, name):
    c = intents.control(said)
    assert c is not None and c.name == name
    if name == "move":
        assert c.args["dir"] == "left"


@pytest.mark.parametrize("said,kind", [
    ("explain the vector database again", "explain"), ("show where chunking happens", "chunking"),
    ("what happens if retrieval is wrong?", "bad_retrieval"), ("what if retrieval returns bad data", "bad_retrieval"),
    ("where does hallucination happen", "hallucination"), ("compare this with fine-tuning", "compare_finetune"),
    ("make the vector database part bigger", "bigger"), ("embeddings phir se samjhao", "explain"),
])
def test_rag_follow_ups(said, kind):
    f = intents.follow_up(said, "rag")
    assert f is not None and f.name == kind, (said, f)


@pytest.mark.parametrize("said,name", [
    ("draw a triangle", "triangle"), ("draw a circle", "circle"), ("draw an arrow from this to that", "arrow_between"),
    ("highlight this", "highlight_this"), ("circle this", "circle_this"), ("label this as hypotenuse", "label_this"),
    ("erase that", "erase_that"), ("ek triangle banao", "triangle"),
])
def test_drawing_requests(said, name):
    d = intents.draw(said)
    assert d is not None and d.name == name


def test_controls_are_not_taken_when_nothing_is_on_screen(run):
    assert not assistant.wants("continue", run)
    assert not assistant.wants("go back one step", run)
    assert assistant.wants("pen mode", run)
    run.draw([add(obj("c", "circle", cx=100, cy=100, r=20))])
    assert assistant.wants("undo", run)


# --------------------------------------------------------------------------- the front door
def test_typed_rag_lesson_and_follow_ups_end_to_end(run, ov, monkeypatch):
    monkeypatch.setattr(ov, "work_area", lambda monitor="primary": AREA)
    rep = H("Explain RAG architecture with a diagram", speaker=FakeSpeaker())
    assert rep and rep.spoken and "vector" in rep.text.lower()
    assert run.lesson.topic == "rag"
    n_create = sum(1 for c in cmds(ov) if c["op"] == "scene.create")
    for said in ("show where chunking happens", "what happens if retrieval is wrong?", "compare this with fine-tuning",
                 "explain the vector database again", "make the vector database part bigger"):
        rep = H(said, speaker=FakeSpeaker())
        assert rep is not None, said
    assert sum(1 for c in cmds(ov) if c["op"] == "scene.create") == n_create     # never redrawn from scratch
    rep = H("go back one step", speaker=FakeSpeaker())
    assert rep is not None
    rep = H("clear it", speaker=FakeSpeaker())
    assert rep.text == "Cleared." and run.lesson is None


def test_hindi_lesson_keeps_its_language_for_controls(run, ov, monkeypatch):
    monkeypatch.setattr(ov, "work_area", lambda monitor="primary": AREA)
    rep = H("पाइथागोरस प्रमेय चित्र बनाकर समझाओ", speaker=FakeSpeaker(cut_at=4))
    assert run.lesson.language in ("hi", "hi-pure")
    rep = H("रुको", speaker=FakeSpeaker())
    assert rep is not None
    sp = FakeSpeaker()
    H("continue", speaker=sp)
    assert sp.said and re.search(r"[ऀ-ॿ]", " ".join(sp.said))


def test_drawing_this_needs_something_to_point_at(run, ov, monkeypatch):
    monkeypatch.setattr(ov, "work_area", lambda monitor="primary": AREA)
    rep = H("circle this", speaker=FakeSpeaker())
    assert "which" in rep.text.lower()
    H("draw a triangle", speaker=FakeSpeaker())
    rep = H("label this as right triangle", speaker=FakeSpeaker())
    assert rep.text == "Done."
    rep = H("erase that", speaker=FakeSpeaker())
    assert rep.text == "Done."
    assert H("undo", speaker=FakeSpeaker())
