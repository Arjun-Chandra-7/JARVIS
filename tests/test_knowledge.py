"""Standing notes load themselves into the prompt when their subject comes up — and not otherwise."""

from __future__ import annotations

import pytest

from jarvis import knowledge


def write(vault, name: str, text: str):
    d = knowledge.directory(vault)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


THESIS = """---
name: thesis
triggers: [thesis, dissertation]
---
The thesis is due 30 November. The advisor is Dr Rao.
"""


def test_frontmatter_is_parsed():
    note = knowledge.parse_note(THESIS, "thesis.md")
    assert note.name == "thesis"
    assert note.triggers == ["thesis", "dissertation"]
    assert "30 November" in note.body
    assert note.always is False


def test_a_comma_separated_trigger_string_also_works():
    note = knowledge.parse_note("---\nname: x\ntriggers: gym, running\n---\nbody", "x.md")
    assert note.triggers == ["gym", "running"]


def test_always_notes_are_recognised():
    note = knowledge.parse_note("---\nname: rules\nalways: true\n---\nBe brief.", "rules.md")
    assert note.always is True


def test_a_note_with_no_triggers_falls_back_to_its_filename():
    # Otherwise the note can never fire, and a note that does nothing is worse than no note.
    note = knowledge.parse_note("no frontmatter, just text", "gym-plan.md")
    assert note.triggers == ["gym plan"]


def test_an_empty_note_is_ignored():
    assert knowledge.parse_note("---\nname: x\n---\n   \n", "x.md") is None


def test_a_note_fires_only_on_its_trigger(tmp_path):
    write(tmp_path, "thesis.md", THESIS)
    assert "Dr Rao" in knowledge.context_for(tmp_path, "how is the thesis going")
    assert "Dr Rao" in knowledge.context_for(tmp_path, "any progress on my DISSERTATION?")
    assert knowledge.context_for(tmp_path, "what's the weather") == ""


def test_always_notes_fire_on_anything(tmp_path):
    write(tmp_path, "style.md", "---\nname: style\nalways: true\n---\nKeep answers under 40 words.")
    assert "40 words" in knowledge.context_for(tmp_path, "literally anything at all")


def test_always_notes_come_first(tmp_path):
    write(tmp_path, "style.md", "---\nname: aaa-style\nalways: true\n---\nStanding rule.")
    write(tmp_path, "thesis.md", THESIS)
    out = knowledge.context_for(tmp_path, "the thesis")
    assert out.index("Standing rule") < out.index("Dr Rao")


def test_the_number_of_injected_notes_is_capped(tmp_path):
    # The point is spending context only on what is relevant; ten notes at once defeats it.
    for i in range(10):
        write(tmp_path, f"n{i}.md", f"---\nname: n{i}\nalways: true\n---\nBody number {i}.")
    out = knowledge.context_for(tmp_path, "anything")
    assert out.count("## n") == knowledge.MAX_NOTES


def test_total_length_is_bounded(tmp_path):
    write(tmp_path, "big.md", "---\nname: big\nalways: true\n---\n" + "x" * 5000)
    assert len(knowledge.context_for(tmp_path, "anything")) < knowledge.MAX_CHARS + 300


def test_no_notes_means_no_block(tmp_path):
    assert knowledge.context_for(tmp_path, "anything") == ""


def test_a_missing_directory_is_not_an_error(tmp_path):
    assert knowledge.load(tmp_path / "nope") == []


def test_the_example_is_seeded_once(tmp_path):
    assert knowledge.ensure_example(tmp_path) is not None
    assert knowledge.ensure_example(tmp_path) is None       # not overwritten on the second call


def test_the_seeded_example_does_not_fire_on_ordinary_speech(tmp_path):
    knowledge.ensure_example(tmp_path)
    assert knowledge.context_for(tmp_path, "what is my cpu usage") == ""
    assert "Replace this file" in knowledge.context_for(tmp_path, "tell me about the example topic")


def test_ensure_vault_seeds_the_directory(tmp_path):
    from jarvis.memory.vault import ensure_vault

    ensure_vault(tmp_path / "v", "Arjun")
    assert (knowledge.directory(tmp_path / "v") / "example.md").exists()
