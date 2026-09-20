"""Searching your own files by what is written in them.

Against a temporary directory rather than the real disk, so the test says the same thing on any
machine — but the sizes and shapes are taken from what the real disk turned out to hold.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.memory import doc_search, documents


@pytest.fixture
def library(tmp_path):
    """A small set of documents with a clear right answer."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "hackathon.md").write_text(
        "# Hackathon\nThe hackathon is on the fourth of October in Bangalore.\n"
        "Team name ideas: Vidya Labs, Pragya Works.\n")
    (tmp_path / "notes" / "groceries.txt").write_text("milk, eggs, coffee, rice\n")
    (tmp_path / "long.md").write_text(
        "Hackathon mentioned once.\n" + "Padding that says nothing at all. " * 200)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "readme.md").write_text("hackathon hackathon hackathon\n")
    return tmp_path


def test_the_right_document_comes_first(library):
    hits = doc_search.search("hackathon bangalore", where=[library])
    assert hits, "found nothing at all"
    assert hits[0].name == "hackathon.md"


def test_a_document_that_matches_nothing_is_not_padded_in(library):
    """Returning five results when two match teaches the model that all five are relevant."""
    names = [h.name for h in doc_search.search("hackathon", where=[library])]
    assert "groceries.txt" not in names


def test_node_modules_is_never_somebody_is_documents(library):
    """It is the densest match in this library and it is still noise."""
    names = [h.name for h in doc_search.search("hackathon", where=[library])]
    assert "readme.md" not in names


def test_the_snippet_shows_why_the_file_matched(library):
    """Not the opening paragraph — the opening paragraph of a PDF is its title page."""
    hit = doc_search.search("Vidya Labs", where=[library])[0]
    assert "Vidya Labs" in hit.snippet


def test_an_empty_query_searches_nothing(library):
    assert doc_search.search("", where=[library]) == []


def test_nothing_found_says_where_it_looked(library):
    text = doc_search.readable("zzzznothing", doc_search.search("zzzznothing", where=[library]))
    assert "Nothing in your documents" in text
    assert "Documents" in text or "Downloads" in text


# --------------------------------------------------------------------------- extraction
def test_a_huge_text_file_is_skipped(tmp_path):
    """A 12 MB .txt is a log, a dump or a mistake, not something anybody reads."""
    big = tmp_path / "huge.txt"
    big.write_text("x" * (documents.MAX_TEXT_BYTES + 1))
    assert documents.extract(big) is None


def test_binary_formats_get_a_much_larger_ceiling():
    """Found on the real disk: an ordinary illustrated guide is 13 MB and holds 23,711
    characters of text. A shared limit threw it out as if it were a dataset."""
    assert documents.MAX_BINARY_BYTES > documents.MAX_TEXT_BYTES * 5


def test_an_unknown_extension_is_not_read(tmp_path):
    odd = tmp_path / "model.safetensors"
    odd.write_bytes(b"\x00" * 64)
    assert documents.extract(odd) is None


def test_an_empty_file_is_not_a_document(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("   \n\n  ")
    assert documents.extract(empty) is None


def test_the_roots_can_be_pointed_somewhere_else(monkeypatch, tmp_path):
    """The escape hatch for a Downloads folder full of identity documents."""
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", str(tmp_path))
    assert documents.roots() == [tmp_path]


def test_walking_stops_at_the_limit(library):
    """A home directory has more files than anyone wants read in one breath."""
    assert len(list(documents.walk([library], limit=2))) == 2
