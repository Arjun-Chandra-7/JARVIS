"""What the agent said, and the link it printed."""
from __future__ import annotations

import pytest

from jarvis.coding import answers

DEPLOYED = """
  Building...
  ✔ Deployed to https://my-app-git-main.vercel.app
  Local:   http://localhost:5173/
  I added the dark mode toggle to the settings page and persisted the choice in localStorage.
  tokens used 4,201
"""


def test_the_sentence_is_said_and_the_chatter_is_not():
    """Read back naively this came out as "Building... Deployed to the link Local: the link
    I added the dark mode toggle..." — every status line on the way, in order."""
    said = answers.spoken_answer(DEPLOYED)
    assert said.startswith("I added the dark mode toggle")
    assert "Building" not in said and "tokens used" not in said


def test_links_are_not_read_aloud():
    """A URL spoken out loud is unusable, and it is being opened anyway."""
    said = answers.spoken_answer(DEPLOYED)
    assert "http" not in said and "the link" not in said


def test_every_link_is_found_in_the_order_printed():
    found = answers.links(DEPLOYED)
    assert found == ["https://my-app-git-main.vercel.app", "http://localhost:5173/"]


def test_a_link_at_the_end_of_a_sentence_loses_the_full_stop():
    assert answers.links("see https://example.com/app.") == ["https://example.com/app"]


def test_the_last_result_wins():
    """Agents print the thing they just made after the ones they mentioned getting there."""
    assert answers.worth_opening(DEPLOYED) == "http://localhost:5173/"


@pytest.mark.parametrize("line, opened", [
    ("running at localhost:3000", "http://localhost:3000"),
    ("preview: https://x-y-z.vercel.app", "https://x-y-z.vercel.app"),
    ("opened https://github.com/a/b/pull/12", "https://github.com/a/b/pull/12"),
    ("see the docs at https://react.dev/learn", None),
    ("no links here at all", None),
])
def test_only_results_are_worth_opening(line, opened):
    """A documentation page quoted while explaining itself is not a result."""
    assert answers.worth_opening(line) == opened


def test_a_bare_host_gets_a_scheme_so_it_can_be_opened():
    assert answers.worth_opening("serving on 127.0.0.1:8080").startswith("http://")


def test_nothing_to_say_is_none_rather_than_empty():
    assert answers.spoken_answer("") is None
    assert answers.spoken_answer("❯\n  tokens used 12\n") is None
