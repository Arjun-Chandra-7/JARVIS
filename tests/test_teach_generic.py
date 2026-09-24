"""Lessons on any topic: the model's JSON checked, laid out and drawn — and the wrong answers
found in the conversation history, each pinned by the sentence that produced it."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from jarvis.teach import assistant, intents
from jarvis.teach import runner as runner_mod
from jarvis.teach.bus import Overlay, _NullTransport, use
from jarvis.teach.lessons import generic
from jarvis.teach.runner import LessonRunner, Spoken

AREA = {"index": 0, "scale": 1.0, "w": 1920, "h": 1080, "work": {"x": 0, "y": 28, "w": 1920, "h": 1052}}

WATER = {
    "title": "The water cycle", "layout": "cycle",
    "nodes": [{"id": "ev", "label": "Evaporation", "sub": "sun heats water", "detail": "The sun warms seas and lakes and water rises as vapour."},
              {"id": "co", "label": "Condensation", "sub": "clouds form", "detail": "High up, the vapour cools into droplets that make clouds."},
              {"id": "pr", "label": "Precipitation", "sub": "rain, snow", "detail": "Droplets join until they are heavy enough to fall."},
              {"id": "cl", "label": "Collection", "sub": "rivers, seas", "detail": "Water gathers in rivers and seas, and the cycle starts again."}],
    "edges": [{"from": "ev", "to": "co"}, {"from": "co", "to": "pr"}, {"from": "pr", "to": "cl"}, {"from": "cl", "to": "ev"}],
    "steps": [{"say": "The sun heats water, and it rises as vapour.", "show": ["ev"], "focus": ["ev"], "note": "Heat lifts water"},
              {"say": "The vapour cools into clouds.", "show": ["co"], "focus": ["co"]},
              {"say": "Heavy droplets fall as rain.", "show": ["pr"], "focus": ["pr"]},
              {"say": "Rivers carry it back to the sea.", "show": ["cl"], "focus": ["cl"]}],
    "summary": "Water keeps moving between sea, sky and land.", "check": "What makes the vapour turn into clouds?",
}


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def speak(self, phrases, on_start):
        for i, p in enumerate(phrases):
            self.said.append(p)
            on_start(i, time.time() * 1000 + 30)
        return Spoken(len(phrases), len(phrases), False)

    def stop(self):
        pass


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


@pytest.fixture()
def model(monkeypatch):
    """The strong model, answering from a queue of canned replies."""
    from jarvis import llm
    replies = []

    async def fake(system, prompt, config=None, temperature=0.2, timeout=60.0, strength="default"):
        text = replies.pop(0) if replies else ""
        return llm.Completion(text=text)
    monkeypatch.setattr(llm, "complete_detailed", fake)
    return replies


def send_all(plan):
    ov = Overlay(_NullTransport())
    ov.new_generation(plan.lesson_id)
    ov.send(plan.setup)
    for s in plan.steps:
        for p in s.phrases:
            ov.send(p.visuals)
    return ov.transport.commands()


# --------------------------------------------------------------------------- the model's JSON
def test_json_is_found_inside_fences_and_chatter():
    raw = "Sure! Here it is:\n```json\n" + json.dumps(WATER) + "\n```\nHope that helps."
    assert generic.parse_json(raw)["title"] == "The water cycle"
    assert generic.parse_json("no json here") is None


def test_unsafe_or_broken_parts_are_dropped_not_drawn():
    bad = json.loads(json.dumps(WATER))
    bad["nodes"].append({"id": "x", "label": "<script>alert(1)</script>"})
    bad["nodes"].append({"id": "y", "label": "see https://evil.example"})
    bad["edges"].append({"from": "ev", "to": "nowhere"})
    bad["steps"][0]["show"].append("ghost")
    bad["layout"] = "hexagon"
    spec = generic.check(bad)
    labels = [n["label"] for n in spec.nodes]
    assert "see https://evil.example" not in labels and all("http" not in l for l in labels)
    assert all("<" not in l for l in labels)
    assert all(e["to"] != "nowhere" for e in spec.edges)
    assert spec.layout == "flow"                                   # unknown layout → the default
    assert generic.check({"nodes": [{"label": "only one"}], "steps": []}) is None


def test_arrows_survive_as_arrows():
    spec = generic.check({**WATER, "formula": "6CO2 + 6H2O -> C6H12O6 + 6O2"})
    assert "→" in spec.formula and ">" not in spec.formula


@pytest.mark.parametrize("layout", generic.LAYOUTS)
def test_every_layout_draws_valid_commands_inside_the_screen(layout):
    raw = {**WATER, "layout": layout, "columns": ["Left", "Right"], "formula": "E = m c^2"}
    raw["nodes"] = [dict(n, side="left" if i < 2 else "right") for i, n in enumerate(WATER["nodes"])]
    plan = generic.build(generic.check(raw), "en", AREA, "water cycle")
    cmds = send_all(plan)                                          # raises if anything is invalid
    nodes = [c["object"] for c in cmds if c["op"] == "shape.add" and c["object"]["type"] == "node"]
    assert {n["id"] for n in nodes} == {"ev", "co", "pr", "cl"}
    w = AREA["work"]
    for n in nodes:
        assert w["x"] <= n["x"] and n["x"] + n["w"] <= w["x"] + w["w"]
        assert w["y"] <= n["y"] and n["y"] + n["h"] <= w["y"] + w["h"]
    assert any(c["op"] == "shape.add" and c["object"]["type"] == "equation" for c in cmds)


def test_edges_never_run_through_another_box():
    raw = {**WATER, "layout": "flow", "edges": [{"from": "ev", "to": "pr"}, {"from": "ev", "to": "co"}]}
    spec = generic.check(raw)
    g = generic.place(spec, AREA)
    bends = generic.route_around(spec, g["boxes"])
    assert bends.get("e-ev-pr") and "e-ev-co" not in bends


# --------------------------------------------------------------------------- requests
@pytest.mark.parametrize("said,subject", [
    ("explain the water cycle with a diagram", "water cycle"),
    ("draw and explain how photosynthesis works", "photosynthesis"),
    ("photosynthesis ko diagram se samjhao", "photosynthesis"),
    ("पानी का चक्र चित्र बनाकर समझाओ", "पानी का चक्र"),
    ("can you visually explain how a transformer neural network works in hindi", "transformer neural network"),
    ("draw a diagram of the OSI model", "OSI model"),
])
def test_any_subject_is_a_lesson(said, subject):
    i = intents.lesson(said)
    assert i and i.name == "topic" and i.args["subject"] == subject


@pytest.mark.parametrize("said", ["draw a triangle", "draw me the mona lisa", "explain photosynthesis", "what is RAG"])
def test_not_lessons(said):
    assert intents.lesson(said) is None


def test_a_lesson_on_any_topic_end_to_end(ov, model):
    model.append(json.dumps(WATER))
    sp = FakeSpeaker()
    rep = asyncio.run(assistant.handle("explain the water cycle with a diagram", speaker=sp))
    assert rep and rep.spoken
    assert sp.said[0] == "Let me draw that out."                  # said at once, before the model answers
    assert "vapour" in " ".join(sp.said)
    r = runner_mod.RUNNER
    assert r.lesson.extras["generic"]
    # "Explain condensation again": that node, its own sentence, no model call.
    sp2 = FakeSpeaker()
    asyncio.run(assistant.handle("explain condensation again", speaker=sp2))
    assert sp2.said == ["High up, the vapour cools into droplets that make clouds."]
    # A free question about the lesson: answered by the model with the lesson as context.
    model.append(json.dumps({"say": "Cold air holds less water, so the vapour turns to droplets.", "focus": ["co"]}))
    sp3 = FakeSpeaker()
    asyncio.run(assistant.handle("why does it turn into clouds?", speaker=sp3))
    assert "Cold air" in sp3.said[0]
    creates = [c for c in ov.transport.commands() if c["op"] == "scene.create"]
    assert len(creates) == 1                                       # follow-ups never redrew it


def test_no_model_says_so_and_draws_nothing(ov, model):
    rep = asyncio.run(assistant.handle("explain the water cycle with a diagram", speaker=FakeSpeaker()))
    assert "no model is reachable" in rep.text and not rep.spoken
    assert not [c for c in ov.transport.commands() if c["op"] == "scene.create"]


def test_nonsense_from_the_model_draws_nothing(ov, model):
    model.append("I'd be happy to help! The water cycle is fascinating.")
    rep = asyncio.run(assistant.handle("explain the water cycle with a diagram", speaker=FakeSpeaker()))
    assert "didn't make sense" in rep.text


def test_lesson_questions_and_live_questions_are_told_apart():
    assert assistant.about_the_lesson("why does it turn into clouds?")
    assert assistant.about_the_lesson("isko hindi mein samjhao")
    assert not assistant.about_the_lesson("what's the time")
    assert not assistant.about_the_lesson("send a whatsapp to papa")


# --------------------------------------------------------------------------- the history's wrong answers
def test_old_messages_are_not_attached_to_unrelated_turns():
    from jarvis.audio.voice_session import about_the_message
    last = {"app": "WhatsApp", "who": "Arnav Pandey", "text": "Kiska hai", "at": time.time()}
    assert not about_the_message("Is Claude completed?", last)                  # the turn in the history
    assert not about_the_message("What is in the food?", last)
    assert about_the_message("reply to him that it's mine", last)
    assert about_the_message("what did Arnav say", last)
    assert not about_the_message("reply to him", {**last, "at": time.time() - 3600})   # stale


def test_tool_names_are_never_said():
    from jarvis.audio.voice_session import speakable
    out = speakable("I don't understand this message. Please use whatsapp_send to reply.")
    assert "whatsapp_send" not in out and "I don't understand this message." in out
    assert "whatsapp" not in speakable('<function=whatsapp_send>{"to":"x"}</function>').lower()


@pytest.mark.parametrize("said", ["Yeah, that's it.", "JARvis, that's it.", "Hey Jarvis, bye", "ok thanks"])
def test_goodbyes_with_lead_ins_end_the_conversation(said):
    from jarvis.audio.conversation import is_session_end
    assert is_session_end(said)


def test_what_was_attached_is_not_logged_as_said():
    from jarvis.hud_state import as_said
    assert as_said("JARvis\n\n(Spoken question, not a request to do anything. Answer it…)") == "JARvis"
    assert as_said('[Most recent incoming WhatsApp message from X: "hi". This is context…]\n\nIs Claude done?') == "Is Claude done?"


def test_a_follow_up_about_what_was_on_screen_keeps_its_material(monkeypatch):
    from jarvis import context, video_command as vc
    monkeypatch.setattr(context, "current", lambda: "t")
    vc._LAST_SCREEN.clear()
    vc.remember_explanation("explain this part of the chapter", "Nicola and Jacopo sell strawberries to save money", "They are hiding...")
    assert vc.follows_explanation("So what values does this show about Nicola and Jippo?", "t")
    assert vc.follows_explanation("यह step English में समझाओ", "t")
    assert not vc.follows_explanation("what's the weather like", "t")
    assert not vc.follows_explanation("message Papa hi", "t")
    vc._LAST_SCREEN["t"]["at"] -= 3600
    assert not vc.follows_explanation("So what values does this show about Nicola?", "t")


def test_find_a_whiteboard_and_draw_is_a_drawing():
    from jarvis.draw_command import parse
    assert parse("Find a free whiteboard site online and draw me the Mona Lisa.").lower() == "mona lisa"
    assert parse("Hey Jarvis, find an online whiteboard site and draw me the Mona Lisa").lower() == "mona lisa"
