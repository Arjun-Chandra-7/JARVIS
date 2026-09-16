"""A follow-up that changes only the subject: after a drawing worked, "now Albert Einstein"."""
from __future__ import annotations

import asyncio

import pytest

from jarvis import chain_command, context


# --------------------------------------------------------------------- follow-ups
def _after(said, subject):
    context.forget("local")
    context.note_action(said, subject)


@pytest.mark.parametrize("follow_up, expected", [
    ("now albert einstein", "find a free whiteboard site and draw me albert einstein"),
    ("and a dragon", "find a free whiteboard site and draw me a dragon"),
    ("how about batman", "find a free whiteboard site and draw me batman"),
    ("what about a tiger", "find a free whiteboard site and draw me a tiger"),
    ("then a castle", "find a free whiteboard site and draw me a castle"),
    ("instead a horse", "find a free whiteboard site and draw me a horse"),
])
def test_a_follow_up_repeats_the_whole_request_with_a_new_subject(follow_up, expected):
    """Not "draw" plus the new subject: the rest of the sentence is what opened the whiteboard."""
    _after("find a free whiteboard site and draw me the mona lisa", "the mona lisa")
    assert context.resolve(follow_up) == expected


@pytest.mark.parametrize("said", [
    "wake up", "scan the room", "go to sleep", "what did i miss", "now what",
])
def test_short_commands_are_not_swallowed_as_follow_ups(said):
    """This was tried without requiring a lead-in word, and any short phrase within a few minutes
    of a successful command became a new subject for it — "wake up" became something to draw."""
    _after("find a free whiteboard site and draw me the mona lisa", "the mona lisa")
    assert context.resolve(said) == said


def test_a_follow_up_carrying_its_own_verb_is_left_alone():
    """"Now open Spotify" is a request in its own right; rebuilding it gives nonsense."""
    _after("open netflix", "netflix")
    assert context.resolve("now open spotify") == "now open spotify"


def test_a_question_is_not_a_new_subject():
    _after("open netflix", "netflix")
    assert context.resolve("now what is the time") == "now what is the time"


def test_nothing_is_rebuilt_when_nothing_was_done():
    context.forget("local")
    assert context.resolve("now albert einstein") == "now albert einstein"


def test_a_stale_request_is_not_carried_on():
    import time
    _after("open netflix", "netflix")
    ctx = context.of("local")
    ctx.at = time.time() - context.STALE_AFTER_S - 1
    assert context.resolve("now spotify") == "now spotify"


def test_repeating_the_same_subject_changes_nothing():
    _after("open netflix", "netflix")
    assert context.resolve("now netflix") == "now netflix"


# --------------------------------------------------------------------- "draw this"
def test_draw_this_means_the_picture_just_generated():
    from jarvis import draw_command
    context.forget("local")
    assert draw_command.parse("draw this") is None, "nothing to point at yet"
    context.note_picture("/tmp/made.png")
    assert draw_command.parse("draw this") == "this"
    assert draw_command.parse("draw it on a whiteboard") == "it"
    context.forget("local")


def test_find_a_surface_and_draw_this():
    from jarvis import find_site
    assert find_site.parse_find_and_draw(
        "find a free whiteboard site and draw this for me") == ("whiteboard", "this")
    assert find_site.parse_find_and_draw(
        "find a free whiteboard site and draw me the mona lisa") == ("whiteboard", "mona lisa")


