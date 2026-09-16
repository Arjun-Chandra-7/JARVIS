"""Reading the files is done here; only the describing is left to the model."""
import pytest

from jarvis import project_command as pc


@pytest.mark.parametrize("said", [
    "read my files and tell me about this project",
    "what is this project about",
    "whats this project about",
    "tell me about this codebase",
    "summarise this project",
    "explain this repo",
    "look at the code and tell me what it does",
])
def test_these_ask_for_a_summary(said):
    assert pc.wants_a_summary(said)


@pytest.mark.parametrize("said", [
    "open netflix", "read my emails", "what is the volume",
    "write a file", "what time is it",
])
def test_these_do_not(said):
    assert not pc.wants_a_summary(said)


def test_it_reads_the_files_that_explain_a_project(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("# Widget\n\nTurns widgets into gadgets.")
    (tmp_path / "noise.log").write_text("x" * 5000)

    from jarvis.integrations import coding

    monkeypatch.setattr(coding, "active_folder", lambda: str(tmp_path))
    monkeypatch.setattr(coding, "overview", lambda folder: "README.md\nnoise.log")

    found = pc.gather()
    assert [name for name, _ in found["files"]] == ["README.md"]
    assert "gadgets" in found["files"][0][1]


def test_a_project_with_nothing_explaining_itself_still_answers(tmp_path, monkeypatch):
    (tmp_path / "main.c").write_text("int main(void){return 0;}")
    from jarvis.integrations import coding

    monkeypatch.setattr(coding, "active_folder", lambda: str(tmp_path))
    monkeypatch.setattr(coding, "overview", lambda folder: "main.c")

    found = pc.gather()
    assert found["files"] == []


def test_no_project_open_is_said_plainly(monkeypatch):
    import asyncio

    from jarvis.integrations import coding

    monkeypatch.setattr(coding, "active_folder", lambda: None)
    said = asyncio.run(pc.handle("what is this project about"))
    assert "don't see a project" in said


def test_what_is_sent_to_the_model_is_bounded():
    """Sent six thousand characters alongside ninety-four tool schemas, the local model returned
    an empty string."""
    assert pc.MAX_CHARS <= 3000
