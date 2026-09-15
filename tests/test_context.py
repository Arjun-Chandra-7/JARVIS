"""A follow-up must be able to refer to the turn before it."""
import time

import pytest

from jarvis import context as ctx


@pytest.fixture(autouse=True)
def _clean():
    ctx.forget("t")
    yield
    ctx.forget("t")


def _seed():
    ctx.note_results(["Friends", "Friends with Benefits", "The Office"], "t")
    ctx.note_opened("t", site="netflix", target="Friends")


@pytest.mark.parametrize("said,expected", [
    ("play the second one", "play Friends with Benefits"),
    ("open the third result", "open The Office"),
    ("click the last one", "click The Office"),
    ("play the first one", "play Friends"),
])
def test_a_position_in_the_last_list_is_filled_in(said, expected):
    """The handlers keep no state of their own, so the request has to arrive complete."""
    _seed()
    assert ctx.resolve(said, "t") == expected


def test_a_pronoun_stands_for_the_last_thing_acted_on():
    _seed()
    assert ctx.resolve("close it", "t") == "close Friends"


def test_a_request_that_names_its_own_target_is_untouched():
    _seed()
    for said in ("open netflix", "what is the volume", "turn the volume up"):
        assert ctx.resolve(said, "t") == said


def test_nothing_is_invented_when_there_is_no_context():
    """A pronoun with nothing behind it is left alone — better that the model asks what was
    meant than that this guesses and is confidently wrong."""
    assert ctx.resolve("close it", "t") == "close it"
    assert ctx.resolve("play the second one", "t") == "play the second one"


def test_a_position_past_the_end_is_not_invented():
    ctx.note_results(["only one"], "t")
    assert ctx.resolve("play the fourth one", "t") == "play the fourth one"


def test_context_goes_stale():
    """An hour later, "play the second one" means a different list."""
    _seed()
    ctx.of("t").at = time.time() - (ctx.STALE_AFTER_S + 1)
    assert ctx.resolve("play the second one", "t") == "play the second one"


def test_sessions_do_not_share_context():
    _seed()
    assert ctx.resolve("play the second one", "other") == "play the second one"


def test_an_activity_can_hold_state_across_turns():
    """What a game in progress needs: somewhere to keep its position."""
    ctx.of("t").activity["chess"] = {"fen": "startpos", "moves": ["e4"]}
    ctx.of("t").touch()
    assert ctx.of("t").activity["chess"]["moves"] == ["e4"]


@pytest.mark.parametrize("said", [
    "that's all",        # how the user says goodnight; rewriting the "that" broke sleep
    "that'll be all",
    "that's it",
    "forget it",
    "stop it",
])
def test_phrases_that_only_look_like_references_are_left_alone(said):
    _seed()
    assert ctx.resolve(said, "t") == said
