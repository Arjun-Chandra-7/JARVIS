"""Catching the word the way it is actually said.

From the real transcript, verbatim:

    you>    open my lindin
    jarvis> Opened Feed | LinkedIn.

The pattern only knew "linkedin", so "lindin" missed it, fell through to the brain, and the brain
did the literal thing — opened the website. The copilot dashboard is the whole point of routing
this at all, and it never got a chance.

Worth noting that the user's own checkout of the copilot is named "Linkdin", which is a fair
indication of how the word arrives.
"""

from __future__ import annotations

import pytest

from jarvis.linkedin_command import _LINKEDIN


@pytest.mark.parametrize("said", [
    "open my lindin",                       # straight from the transcript
    "open my professional lindin dashboard",
    "open my linkdin",
    "show me my linkden",
    "open my linkedin",
    "open my linked in",
    "my linked-in profile",
    "check my LinkedIn drafts",
])
def test_the_word_is_caught_however_it_arrives(said):
    assert _LINKEDIN.search(said) is not None


@pytest.mark.parametrize("said", [
    "open netflix",
    "link the two files",
    "what's in my inbox",
    "open my calendar",
])
def test_other_requests_are_not_swallowed(said):
    """A pattern loose enough to catch every mishearing is also loose enough to catch things it
    should not, so this is the half that keeps it honest."""
    assert _LINKEDIN.search(said) is None
