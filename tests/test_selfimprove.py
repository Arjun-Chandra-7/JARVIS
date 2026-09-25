"""The failure journal: what self-repair reads as evidence. (The guards on a change itself —
frozen reproduction tests, the diff boundary — are in test_selfrepair.py.)"""
import os
import time

import pytest

from jarvis.selfimprove import journal


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


def test_a_conversation_left_open_for_hours_is_not_one_conversation():
    """At 16:41 "How you doing?" was answered "I found several matches for 'Arnav Pandey'" — a
    reply to an unresolved question from a different sitting, still in the last ten messages
    because trimming counted turns and never looked at the clock."""
    import time as _time

    from jarvis.agent.groq_core import GroqAgent

    agent = GroqAgent.__new__(GroqAgent)
    agent.messages = [{"role": "system", "content": "s"}]
    agent._turn_times = {}

    old = {"role": "user", "content": "[time: x] who is arnav"}
    agent.messages.append(old)
    agent._turn_times[id(old)] = _time.time() - 3600
    agent.messages.append({"role": "assistant", "content": "Several matches for Arnav Pandey..."})

    fresh = {"role": "user", "content": "[time: y] how are you doing"}
    agent.messages.append(fresh)
    agent._turn_times[id(fresh)] = _time.time()

    agent._drop_stale()
    kept = [m["content"] for m in agent.messages[1:]]
    assert kept == ["[time: y] how are you doing"]
