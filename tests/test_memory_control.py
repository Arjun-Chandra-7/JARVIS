"""Controllable memory: provenance must be accurate, and corrections must be auditable."""
import pytest

from jarvis.memory import control


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "Jarvis").mkdir()
    (tmp_path / "People").mkdir()
    (tmp_path / "private").mkdir()
    (tmp_path / "Jarvis" / "profile.md").write_text(
        "# Profile\n"
        "- Name: Arjun\n"
        "- Location / timezone: Bangalore, India (IST, UTC+5:30)\n"
        "- Prefers concise answers\n",
        encoding="utf-8",
    )
    (tmp_path / "Jarvis" / "journal.md").write_text(
        "# Journal\n- Asked about the weather in Bangalore\n", encoding="utf-8"
    )
    (tmp_path / "People" / "Rahul.md").write_text(
        "- Relationship: brother\n- Lives in Bangalore\n", encoding="utf-8"
    )
    (tmp_path / "private" / "whatsapp.md").write_text(
        "- Secret chat mentioning Bangalore\n", encoding="utf-8"
    )
    return tmp_path


# ------------------------------------------------------------------ search
def test_search_finds_matching_lines(vault):
    hits = control.search(vault, "bangalore")
    assert hits
    assert any("Bangalore" in h.snippet for h in hits)


def test_search_reports_file_and_line(vault):
    hits = control.search(vault, "timezone")
    assert hits
    h = hits[0]
    assert h.path.endswith("profile.md")
    assert h.line == 3
    assert h.source.endswith(":3")
    assert h.recorded, "a memory without a date cannot be judged for staleness"


def test_profile_is_explicit_not_inferred(vault):
    """profile.md holds what the user stated, even though it sits under the Jarvis folder."""
    hits = control.search(vault, "timezone")
    assert hits[0].kind == "explicit"


def test_journal_is_inferred(vault):
    hits = control.search(vault, "asked about the weather")
    assert hits and hits[0].kind == "inferred"


def test_people_notes_are_explicit(vault):
    hits = control.search(vault, "brother")
    assert hits and hits[0].kind == "explicit"


def test_private_directory_is_never_searched(vault):
    """Imported chat history is excluded from ordinary recall; this must not be a way around that."""
    hits = control.search(vault, "secret chat")
    assert not any("private" in h.path for h in hits)


def test_empty_query_returns_nothing(vault):
    assert control.search(vault, "") == []
    assert control.search(vault, "   ") == []


def test_headings_are_not_returned_as_memories(vault):
    hits = control.search(vault, "profile")
    assert all(not h.snippet.startswith("#") for h in hits)


def test_results_are_ranked_with_explicit_first(vault):
    hits = control.search(vault, "bangalore")
    kinds = [h.kind for h in hits]
    if "explicit" in kinds and "inferred" in kinds:
        assert kinds.index("explicit") < kinds.index("inferred")


# ------------------------------------------------------------------ correct
def test_correct_replaces_the_line(vault, monkeypatch):
    monkeypatch.setattr(control.vaultmod, "git_autocommit", lambda *a, **k: True)
    res = control.correct(vault, "Jarvis/profile.md",
                          "- Location / timezone: Bangalore, India (IST, UTC+5:30)",
                          "- Location / timezone: Pune, India (IST, UTC+5:30)")
    assert res["ok"], res
    text = (vault / "Jarvis" / "profile.md").read_text()
    assert "Pune" in text
    assert "Bangalore, India (IST" not in text.split("<!--")[0]


def test_correct_keeps_the_old_value_for_audit(vault, monkeypatch):
    monkeypatch.setattr(control.vaultmod, "git_autocommit", lambda *a, **k: True)
    control.correct(vault, "Jarvis/profile.md", "- Name: Arjun", "- Name: Arjun Chandra")
    text = (vault / "Jarvis" / "profile.md").read_text()
    assert "corrected" in text and "- Name: Arjun\"" in text.replace("“", '"').replace("”", '"')


def test_correct_requires_replacement_text(vault):
    res = control.correct(vault, "Jarvis/profile.md", "- Name: Arjun", "   ")
    assert not res["ok"]


def test_correct_reports_when_the_line_moved(vault):
    res = control.correct(vault, "Jarvis/profile.md", "- Nonexistent line", "x")
    assert not res["ok"]
    assert "no longer" in res["message"]


def test_correct_refuses_to_write_outside_the_vault(vault, tmp_path):
    outside = tmp_path.parent / "escape.md"
    outside.write_text("- secret\n", encoding="utf-8")
    res = control.correct(vault, "../escape.md", "- secret", "- changed")
    assert not res["ok"]
    assert outside.read_text() == "- secret\n", "wrote outside the vault"


# ------------------------------------------------------------------ forget
def test_forget_removes_the_line(vault, monkeypatch):
    monkeypatch.setattr(control.vaultmod, "git_autocommit", lambda *a, **k: True)
    res = control.forget(vault, "Jarvis/profile.md", "- Prefers concise answers")
    assert res["ok"]
    assert "Prefers concise answers" not in (vault / "Jarvis" / "profile.md").read_text()


def test_forget_is_honest_about_git_history(vault, monkeypatch):
    monkeypatch.setattr(control.vaultmod, "git_autocommit", lambda *a, **k: True)
    res = control.forget(vault, "Jarvis/profile.md", "- Name: Arjun")
    assert "git history" in res["message"], "must not imply the data is gone entirely"


def test_forget_missing_line_is_reported(vault):
    res = control.forget(vault, "Jarvis/profile.md", "- not there")
    assert not res["ok"]


def test_forget_leaves_other_lines_intact(vault, monkeypatch):
    monkeypatch.setattr(control.vaultmod, "git_autocommit", lambda *a, **k: True)
    control.forget(vault, "Jarvis/profile.md", "- Prefers concise answers")
    text = (vault / "Jarvis" / "profile.md").read_text()
    assert "- Name: Arjun" in text and "Bangalore" in text
