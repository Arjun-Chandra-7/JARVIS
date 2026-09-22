"""Reading an agent's own account of its turn, instead of guessing from processor load.

Against transcripts written into a temp directory in the real format, because the format is the
thing being relied on and a fixture that invents its own shape tests nothing.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from jarvis.coding import transcripts


def write_transcript(tmp_path, cwd, records, *, name="session.jsonl", age_s=0.0):
    directory = tmp_path / transcripts.slug_for(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    if age_s:
        when = time.time() - age_s
        os.utime(path, (when, when))
    return path


def assistant(stop_reason, uuid="u1", text="All done."):
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": "2026-09-22T09:00:00.000Z",
        "message": {
            "role": "assistant",
            "stop_reason": stop_reason,
            "content": [{"type": "text", "text": text}],
        },
    }


@pytest.fixture(autouse=True)
def projects_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(transcripts, "PROJECTS", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- the slug
@pytest.mark.parametrize("cwd,expected", [
    ("/home/xor_sensei/Madara/Dev/Jarvis", "-home-xor-sensei-Madara-Dev-Jarvis"),
    # The two that are easy to miss: the dot and the underscore both become dashes, which is why
    # ".claude" turns into "--claude".
    ("/home/xor_sensei/Madara/Dev/Jarvis/.claude/worktrees/gaps",
     "-home-xor-sensei-Madara-Dev-Jarvis--claude-worktrees-gaps"),
])
def test_the_project_directory_is_the_path_with_every_separator_dashed(cwd, expected):
    assert transcripts.slug_for(cwd) == expected


# --------------------------------------------------------------------------- reading a turn
def test_end_turn_is_a_finished_turn(tmp_path):
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [assistant("end_turn", uuid="abc")])
    turn = transcripts.state_for(cwd)
    assert turn is not None
    assert turn.kind == "done"
    assert turn.completion_id == "abc"


def test_tool_use_is_still_working(tmp_path):
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [assistant("tool_use")])
    assert transcripts.state_for(cwd).kind == "working"


def test_a_long_silence_after_a_tool_call_is_still_working(tmp_path):
    """Tried as "it must be asking", and wrong within two minutes of running: a slow tool call
    went quiet for 75 seconds and the rule announced a permission prompt that did not exist. A
    long build and a permission prompt are identical from out here."""
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [assistant("tool_use")], age_s=600)
    assert transcripts.state_for(cwd).kind == "working"


def test_a_turn_that_has_not_ended_has_no_completion_id(tmp_path):
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [assistant("tool_use")])
    assert transcripts.state_for(cwd).completion_id == ""


def test_the_newest_assistant_message_decides(tmp_path):
    """A turn that ended and then started again is working, not finished."""
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [
        assistant("end_turn", uuid="old"),
        {"type": "user", "uuid": "u", "message": {"role": "user", "content": "next"}},
        assistant("tool_use", uuid="new"),
    ])
    assert transcripts.state_for(cwd).kind == "working"


def test_records_without_a_stop_reason_are_skipped(tmp_path):
    """Streaming records arrive without one; they are not the end of anything."""
    cwd = "/home/u/project"
    partial = assistant(None, uuid="streaming")
    write_transcript(tmp_path, cwd, [assistant("end_turn", uuid="real"), partial])
    assert transcripts.state_for(cwd).completion_id == "real"


# --------------------------------------------------------------------------- not over-reporting
def test_no_transcript_means_no_opinion(tmp_path):
    """Silence, so the CPU ladder gets its say for agents that write nothing."""
    assert transcripts.state_for("/home/u/never-used") is None


def test_a_transcript_nobody_has_touched_in_hours_is_forgotten(tmp_path):
    """Otherwise every stale project directory on the disk reports a finished turn for ever."""
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [assistant("end_turn")],
                     age_s=transcripts.FORGET_AFTER_S + 60)
    assert transcripts.state_for(cwd) is None


def test_a_half_written_final_line_does_not_break_the_read(tmp_path):
    """The file is appended to live; a read can land mid-line."""
    cwd = "/home/u/project"
    path = write_transcript(tmp_path, cwd, [assistant("end_turn", uuid="abc")])
    with open(path, "a") as fh:
        fh.write('{"type": "assistant", "message": {"role": "assist')
    assert transcripts.state_for(cwd).completion_id == "abc"


def test_the_newest_file_in_a_directory_wins(tmp_path):
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [assistant("tool_use")], name="old.jsonl", age_s=600)
    write_transcript(tmp_path, cwd, [assistant("end_turn", uuid="fresh")], name="new.jsonl")
    assert transcripts.state_for(cwd).completion_id == "fresh"


# --------------------------------------------------------------------------- the answer
def test_the_final_answer_comes_back_when_the_turn_has_ended(tmp_path):
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [assistant("end_turn", text="Tests pass, sir.")])
    assert transcripts.final_answer(cwd) == "Tests pass, sir."


def test_there_is_no_final_answer_mid_turn(tmp_path):
    """Speaking a half-finished thought is worse than saying nothing."""
    cwd = "/home/u/project"
    write_transcript(tmp_path, cwd, [assistant("tool_use", text="Let me check")])
    assert transcripts.final_answer(cwd) is None


def test_text_is_taken_out_of_a_mixed_content_block(tmp_path):
    """A final message can carry tool results beside its prose."""
    cwd = "/home/u/project"
    record = assistant("end_turn")
    record["message"]["content"] = [
        {"type": "thinking", "thinking": "hidden"},
        {"type": "text", "text": "Seventeen tests passed."},
    ]
    write_transcript(tmp_path, cwd, [record])
    assert transcripts.final_answer(cwd) == "Seventeen tests passed."
