"""Two capabilities in one sentence: "generate an image of Iron Man, then draw it on a whiteboard"."""
from __future__ import annotations

import asyncio

import pytest

from jarvis import chain_command, context


# --------------------------------------------------------------------- splitting
@pytest.mark.parametrize("said, parts", [
    ("generate an image of a fox, then draw it on a whiteboard",
     ["generate an image of a fox", "draw it on a whiteboard"]),
    ("open youtube then search for lofi", ["open youtube", "search for lofi"]),
    ("make a picture of a dragon and then draw it", ["make a picture of a dragon", "draw it"]),
    ("open netflix, after that open spotify", ["open netflix", "open spotify"]),
])
def test_a_chain_comes_apart_at_the_joins(said, parts):
    assert chain_command.split(said) == parts


def test_a_bare_and_is_not_a_join():
    """"Find a free whiteboard site and draw me the Mona Lisa" is one request that one handler
    answers as a unit — it finds the surface and draws on it. Splitting it would leave a half
    that draws on whatever happened to be in front of it."""
    said = "find a free whiteboard site and draw me the mona lisa"
    assert chain_command.split(said) == [said]


def test_trailing_courtesy_is_not_a_step():
    assert chain_command.split("open netflix then please") == ["open netflix"]


# --------------------------------------------------------------------- claiming
def test_a_sentence_nobody_claims_is_left_to_the_model():
    """"Open the door then tell me a joke" is two clauses and no capabilities."""
    assert asyncio.run(chain_command.handle("open the door then tell me a joke", None)) is None


def test_a_chain_is_refused_whole_when_one_part_is_not_understood():
    """Half a chain carried out is worse than none: the picture would be made and then orphaned."""
    context.forget("local")
    said = "generate an image of a fox then recite me a poem"
    assert asyncio.run(chain_command.handle(said, None)) is None


def test_one_part_is_not_a_chain():
    assert asyncio.run(chain_command.handle("open netflix", None)) is None


def test_a_failing_part_stops_the_rest(monkeypatch):
    """A chain that carries on past a failure reports success for work that never happened."""
    ran = []

    async def first(text, config):
        ran.append(text)
        return "I couldn't find a whiteboard that actually worked, sir."

    async def second(text, config):
        ran.append(text)
        return "Drew it."

    monkeypatch.setattr(chain_command, "_claims", lambda name, part, config: name == "open")
    from jarvis import commands
    monkeypatch.setattr(commands, "deterministic_handlers",
                        lambda: [("open", first if not ran else second)])
    reply = asyncio.run(chain_command.handle("open one then open two", None))
    assert "couldn't" in reply
    assert len(ran) == 1, "the second part ran after the first had failed"


@pytest.mark.parametrize("reply, is_failure", [
    ("I couldn't find a picture of a fox to work from.", True),
    ("There is no drawing surface on this page, sir.", True),
    ("I can't make pictures right now — the model isn't installed", True),
    ("Drew mona lisa — 40 strokes, 900 points.", False),
    ("Opened Netflix.", False),
])
def test_which_answers_read_as_failures(reply, is_failure):
    assert chain_command._reads_as_a_failure(reply) is is_failure


