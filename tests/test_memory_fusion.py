"""Merging two rankings, and scoring keywords like a search engine rather than a grep.

The property worth pinning is the one the whole design rests on: agreement outranks enthusiasm.
A result both halves ranked second should beat one that only a single half ranked first, because
two independent methods pointing at the same document is stronger evidence than one method being
very sure.
"""

from __future__ import annotations

import pytest

from jarvis.memory import fuse


# --------------------------------------------------------------------------- BM25
def test_a_document_with_more_of_the_query_wins():
    docs = [
        "The hackathon is on the fourth of October in Bangalore.",
        "Reminder: hackathon team name ideas. hackathon hackathon hackathon.",
    ]
    scores = fuse.bm25_scores("hackathon bangalore", docs)
    assert scores[0] > scores[1], "repeating one term beat covering both"


def test_repeating_a_word_saturates():
    """The fourth mention of a word says much less than the first, which is the difference
    between BM25 and counting."""
    once = fuse.bm25_scores("hackathon", ["hackathon"])[0]
    many = fuse.bm25_scores("hackathon", ["hackathon hackathon hackathon hackathon"])[0]
    assert many > once
    assert many < once * 2, "term frequency grew linearly; it should saturate"


def test_a_long_rambling_note_does_not_win_by_being_long():
    docs = [
        "Hackathon: fourth of October.",
        "Hackathon mentioned once. " + "padding words that say nothing at all. " * 40,
    ]
    scores = fuse.bm25_scores("hackathon", docs)
    assert scores[0] > scores[1], "length normalisation is not working"


def test_a_word_in_every_document_does_not_decide_anything():
    """A term that appears everywhere carries no information, and must not push results down."""
    docs = ["the meeting is at ten", "the meeting is at four", "the meeting moved"]
    scores = fuse.bm25_scores("the", docs)
    assert all(score >= 0 for score in scores), "a universal term scored negative"


def test_no_query_and_no_documents_are_both_survivable():
    assert fuse.bm25_scores("", ["a"]) == [0.0]
    assert fuse.bm25_scores("a", []) == []


# --------------------------------------------------------------------------- fusion
def test_agreement_outranks_enthusiasm():
    """The property the whole design rests on. 'y' is second in both lists; 'x' and 'z' are each
    first in one. Two methods agreeing is stronger evidence than one being very sure."""
    assert fuse.fuse(["x", "y"], ["z", "y"])[0] == "y"


def test_a_result_only_one_half_found_is_not_punished():
    """It gets one contribution instead of two — not a penalty for the other half's silence."""
    order = fuse.fuse(["a", "b", "c"], ["c", "a", "d"])
    assert "d" in order, "a keyword-only result was dropped"
    assert order[0] == "a", "the result both halves ranked highly should lead"


def test_the_order_is_stable_for_the_same_inputs():
    """Ties are broken the same way twice, so the same question does not shuffle its answer."""
    first = fuse.fuse(["a", "b"], ["c", "d"])
    second = fuse.fuse(["a", "b"], ["c", "d"])
    assert first == second


def test_one_empty_half_still_returns_the_other():
    """Ollama being down must not mean no recall at all."""
    assert fuse.fuse([], ["a", "b"]) == ["a", "b"]
    assert fuse.fuse(["a", "b"], []) == ["a", "b"]


@pytest.mark.parametrize("k", [1, 10, 60, 200])
def test_the_damping_constant_never_reorders_a_unanimous_ranking(k):
    """If both halves agree completely, k cannot change the answer — a useful sanity check that
    the constant is a damping term and not a thumb on the scale."""
    assert fuse.fuse(["a", "b", "c"], ["a", "b", "c"], k=k) == ["a", "b", "c"]


def test_tokenizing_keeps_what_a_search_needs():
    assert fuse.tokenize("Don't split it's — Vidya Labs, 2026!") == [
        "don't", "split", "it's", "vidya", "labs", "2026"]
