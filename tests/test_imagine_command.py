"""Which sentences ask for a picture to be generated — and, just as important, which do not."""
from __future__ import annotations

import asyncio

import pytest

from jarvis import imagine_command as ic


@pytest.mark.parametrize("said, subject", [
    ("generate an image of a fox", "a fox"),
    ("create an image of a snowy mountain", "a snowy mountain"),
    ("make me a picture of a sunset over tokyo", "a sunset over tokyo"),
    ("could you generate a picture of a cat", "a cat"),
    ("render an image showing a storm", "a storm"),
    ("an image of a dragon", "a dragon"),
    ("i want an image of a robot", "a robot"),
])
def test_these_ask_for_a_picture(said, subject):
    assert ic.parse(said) == subject


@pytest.mark.parametrize("said", [
    "draw me a fox",                 # the whiteboard's, not ours
    "sketch a house",
    "paint a sunset",
    "generate a report on sales",    # a making verb, but not a picture
    "create a new file",
    "make me a coffee",
    "imagine that i was rich",       # reflection
    "imagine if it rained",
    "take a picture of my screen",   # a screenshot
    "show me a picture of the last one",
    "what does this picture show",
])
def test_these_do_not(said):
    assert ic.parse(said) is None


def test_the_kind_of_picture_survives_into_the_description():
    """"A painting of a lighthouse" should come out looking like a painting."""
    assert ic.parse("create a painting of a lighthouse") == "a lighthouse, painting"
    assert ic.parse("design a logo for a coffee shop") == "a coffee shop, logo"


def test_a_plain_image_gains_no_style_words():
    assert ic.parse("generate an image of a fox") == "a fox"


def test_the_whiteboard_verbs_are_left_alone():
    """Both handlers run on every turn; if they overlapped one would silently shadow the other."""
    from jarvis import draw_command
    for said in ("draw a fox", "sketch a fox on the whiteboard", "paint a sunset"):
        assert draw_command.parse(said) is not None
        assert ic.parse(said) is None
    for said in ("generate an image of a fox", "make me a picture of a fox"):
        assert ic.parse(said) is not None
        assert draw_command.parse(said) is None


class _Recorder:
    def __init__(self):
        self.kwargs = None

    def __call__(self, subject, **kw):
        self.kwargs = dict(kw, subject=subject)
        import types
        from pathlib import Path
        return types.SimpleNamespace(path=Path("/tmp/x.png"), seconds=7.4, steps=kw.get("steps"),
                                     size=kw.get("size"))


@pytest.fixture
def recorder(monkeypatch):
    from jarvis.vision import imagine
    rec = _Recorder()
    monkeypatch.setattr(imagine, "generate", rec)
    monkeypatch.setattr(ic.subprocess, "Popen", lambda *a, **k: None)
    return rec


def test_a_plain_request_is_the_quick_one(recorder):
    asyncio.run(ic.handle("generate an image of a fox"))
    assert recorder.kwargs["steps"] == 1
    assert recorder.kwargs["size"] == 512


def test_asking_for_detail_buys_steps(recorder):
    """Four steps is 25s against 7.5s, so nobody waits that long without having asked."""
    asyncio.run(ic.handle("generate a detailed image of a fox"))
    assert recorder.kwargs["steps"] == 4


def test_a_wallpaper_is_made_big_even_though_the_word_is_stripped(recorder):
    """Parsing removes "wallpaper" from the subject; the size hint has to read the sentence."""
    asyncio.run(ic.handle("make a wallpaper of deep space"))
    assert recorder.kwargs["size"] == 768


def test_a_missing_model_is_said_plainly_rather_than_crashing(monkeypatch):
    from jarvis.vision import imagine

    def explode(*a, **k):
        raise imagine.Unavailable("the image model isn't installed")

    monkeypatch.setattr(imagine, "generate", explode)
    reply = asyncio.run(ic.handle("generate an image of a fox"))
    assert "can't make pictures" in reply and "isn't installed" in reply


def test_a_sentence_that_is_not_ours_is_handed_back_untouched():
    assert asyncio.run(ic.handle("draw me a fox")) is None


# --------------------------------------------------------------- offered to the model, or not
def _tool_names(monkeypatch, ready: bool):
    from jarvis.agent.groq_tools import build_registry
    from jarvis.config import Config
    from jarvis.vision import imagine
    monkeypatch.setattr(imagine, "ready", lambda: ready)
    schemas, _ = build_registry(Config(), None, None)
    return [s["function"]["name"] for s in schemas]


def test_the_tool_is_offered_when_the_weights_are_here(monkeypatch):
    assert "generate_image" in _tool_names(monkeypatch, True)


def test_the_tool_is_withheld_when_they_are_not(monkeypatch):
    """Offering it without weights teaches the model to promise pictures it cannot make."""
    assert "generate_image" not in _tool_names(monkeypatch, False)


def test_the_artist_asks_for_the_tool_that_exists():
    """The roster names tools by string; a typo there is silent until someone asks for a picture."""
    from jarvis.agent.groq_tools import build_registry
    from jarvis.brains.roster import BY_NAME
    from jarvis.config import Config
    schemas, _ = build_registry(Config(), None, None)
    offered = {s["function"]["name"] for s in schemas}
    assert "generate_image" in BY_NAME["artist"].tools
    assert "generate_image" in offered
