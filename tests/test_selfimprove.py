"""A programme that edits itself must not be able to talk itself into a bad change."""
import os
import time

import pytest

from jarvis.selfimprove import improve, journal


@pytest.fixture(autouse=True)
def _own_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_JOURNAL", str(tmp_path / "failures.jsonl"))
    yield


def test_a_failure_survives_being_written_and_read():
    journal.record("tool_failed", "set brightness to 30", "nvidia_0 is owned by video", "set_brightness")
    got = journal.read()
    assert len(got) == 1 and got[0].where == "set_brightness"


def test_one_bad_day_is_not_a_defect():
    """Something that goes wrong once may have been the weather."""
    journal.record("tool_failed", "a", "the network was down", "web_search")
    assert journal.recurring(minimum=2) == []


def test_the_same_problem_twice_is_worth_looking_at():
    for _ in range(3):
        journal.record("task_step_failed", "play x on youtube",
                       "Opera GX is running without control enabled", "open_site")
    pairs = journal.recurring(minimum=2)
    assert len(pairs) == 1 and pairs[0][0] == 3


def test_the_same_fault_from_different_requests_groups_together():
    journal.record("task_step_failed", "play mkbhd on youtube", "nothing to open", "open_first_result")
    journal.record("task_step_failed", "play veritasium on youtube", "nothing to open", "open_first_result")
    assert journal.recurring(minimum=2)[0][0] == 2


def test_different_faults_do_not_group():
    journal.record("tool_failed", "a", "the backlight is not writable", "set_brightness")
    journal.record("tool_failed", "b", "there is no such application", "open_app")
    assert journal.recurring(minimum=2) == []


def test_old_failures_are_not_dug_up():
    journal.record("tool_failed", "a", "same thing", "x")
    journal.record("tool_failed", "a", "same thing", "x")
    assert journal.recurring(minimum=2, since_s=0) == []


def test_recording_never_raises(monkeypatch):
    """A problem writing the journal must not become a second problem."""
    monkeypatch.setenv("JARVIS_JOURNAL", "/proc/this/cannot/be/written")
    journal.record("tool_failed", "a", "b", "c")      # must not raise


# ------------------------------------------------------------------ the guards on a change
def test_a_suite_that_passes_because_the_test_was_deleted_is_rejected(monkeypatch, tmp_path):
    """The whole point of running the tests is lost if the failing one can be removed."""
    monkeypatch.setattr(improve, "_git",
                        lambda *a, **k: type("R", (), {"stdout": "0\t40\ttests/test_thing.py\n",
                                                       "returncode": 0})())
    assert improve._tests_were_removed(tmp_path)


def test_adding_tests_is_not_deleting_them(monkeypatch, tmp_path):
    monkeypatch.setattr(improve, "_git",
                        lambda *a, **k: type("R", (), {"stdout": "52\t0\ttests/test_thing.py\n",
                                                       "returncode": 0})())
    assert not improve._tests_were_removed(tmp_path)


def test_a_small_edit_to_a_test_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(improve, "_git",
                        lambda *a, **k: type("R", (), {"stdout": "6\t3\ttests/test_thing.py\n",
                                                       "returncode": 0})())
    assert not improve._tests_were_removed(tmp_path)


def test_the_brief_carries_the_real_failure():
    f = journal.Failure(kind="tool_failed", request="set brightness to 30",
                        detail="nvidia_0 is owned by the video group", where="set_brightness")
    brief = improve._brief(f, 4)
    assert "4 times" in brief
    assert "set brightness to 30" in brief and "nvidia_0" in brief
    assert "Do not delete or weaken any existing test" in brief
    assert "do not push" in brief.lower()


def test_it_asks_for_the_model_and_effort_that_were_chosen():
    assert improve.MODEL == "opus"
    assert improve.EFFORT == "medium"
