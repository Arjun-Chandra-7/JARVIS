"""Whole repairs, end to end, in a miniature Jarvis checkout: a real worktree, real test runs in
the sandbox, a real commit and a real fast-forward — with stand-ins only for systemd and the
health endpoints. Each test is one of the outcomes the owner can hear."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.selfrepair import activate, jobs, policy, sandbox
from jarvis.selfrepair.editor import Editor, RecipeEditor
from jarvis.selfrepair.jobs import JobStore
from jarvis.selfrepair.pipeline import (COULD_NOT_REPRODUCE, NEEDS_APPROVAL, ROLLED_BACK, TESTS_FAILED,
                                        Pipeline)

BWRAP = shutil.which("bwrap") is not None

SPEECH_TEXT = '''def percent(n):
    """How a percentage is read out."""
    return f"{n}%"
'''
FIXED = '''def percent(n):
    """How a percentage is read out."""
    return f"{n} percent"
'''
REPRO = '''from jarvis.audio.speech_text import percent


def test_a_percentage_is_spoken_as_a_word():
    # Heard live: "battery at 80%" was read out as "eighty per-cent-sign".
    assert percent(80) == "80 percent"
'''


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def live(tmp_path):
    root = tmp_path / "live"
    for rel, text in {
        "jarvis/__init__.py": "", "jarvis/audio/__init__.py": "",
        "jarvis/selfrepair/policy.py": "# marker\n",
        "jarvis/audio/speech_text.py": SPEECH_TEXT,
        "jarvis/integrations/__init__.py": "",
        "jarvis/integrations/contacts.py": "def resolve(name):\n    return name\n",
        "tests/test_speech_text.py": "from jarvis.audio.speech_text import percent\n\n"
                                     "def test_it_returns_text():\n    assert isinstance(percent(1), str)\n",
        "tests/test_ok.py": "def test_ok():\n    assert True\n",
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


class Services(activate.Services):
    def __init__(self):
        self.restarts = []

    def restart(self, units):
        self.restarts.append(list(units))
        return {u: True for u in units}

    def is_active(self, unit):
        return True


class Health(activate.HealthChecker):
    def __init__(self, *results):
        self.results = list(results)

    def check(self, units, commit, since):
        return activate.Health(self.results.pop(0) if self.results else True, [])


def make(live, tmp_path, recipe=None, editor=None, health=None, auto=True):
    store = JobStore()
    said = []
    services = Services()
    act = activate.Activator(live, services, health or Health(True), lock_root=tmp_path)
    pipe = Pipeline(store, live, editor or RecipeEditor(recipe), act, notify=lambda job, text: said.append(text),
                    python=sys.executable, auto_activate=auto, use_bwrap=BWRAP)
    return store, pipe, said, services


def new_job(store, component="spoken text formatting", label="small_repair"):
    comp = policy.component_named(component)
    job = store.create("percentages are read out as a sign", "voice", label, component=comp.name,
                       tier=2 if comp.protected else 1, policy_digest=policy.digest())
    store.transition(job.id, jobs.CLASSIFIED)
    return job


GOOD = {"repro": {"@repro": REPRO},
        "fix": [{"path": "jarvis/audio/speech_text.py", "old": 'return f"{n}%"', "new": 'return f"{n} percent"'}]}


def test_case_c_a_safe_repair_is_reproduced_fixed_tested_committed_activated_and_verified(live, tmp_path):
    store, pipe, said, services = make(live, tmp_path, GOOD)
    job = new_job(store)
    job = pipe.run(job.id)
    assert job.state == jobs.COMPLETED, job.outcome
    assert job.outcome.startswith("Done, sir.") and "verified it against the failing case" in job.outcome
    before, after = job.last_test("focused before"), job.last_test("focused after")
    assert before["failed"] == 1 and not before["ok"] and after["ok"] and after["passed"] == 1
    assert job.last_test("regression after")["ok"]
    assert job.changed_files == ["jarvis/audio/speech_text.py", f"tests/test_repair_{job.id.replace('-', '_')}.py"]
    assert (live / "jarvis/audio/speech_text.py").read_text() == FIXED
    assert sandbox.head(live) == job.candidate_commit
    assert job.branch.startswith(f"jarvis/repair/{job.id}-")
    assert not Path(job.worktree).exists()                                  # worktree cleaned up
    assert job.branch in git(live, "branch", "--list", "jarvis/repair/*")   # branch kept
    assert services.restarts == [["jarvis-voice"]]
    assert "Applying it now" in said[0] and said[-1] == job.outcome
    assert "reproduction test on the live checkout: 1 passed" in job.activation["probe"]
    assert [h["state"] for h in job.history] == [
        "received", "classified", "gathering_evidence", "reproducing", "proposed", "editing", "testing",
        "awaiting_activation", "activating", "health_checking", "completed"]


def test_case_c_rollback_when_the_live_system_disagrees(live, tmp_path):
    store, pipe, said, _ = make(live, tmp_path, GOOD, health=Health(False, True))
    job = pipe.run(new_job(store).id)
    assert job.state == jobs.ROLLED_BACK and job.outcome == ROLLED_BACK
    assert job.rollback["ok"] is True
    assert (live / "jarvis/audio/speech_text.py").read_text() == SPEECH_TEXT
    assert git(live, "log", "-1", "--format=%s").startswith("Revert")


def test_nothing_to_reproduce_means_nothing_changed(live, tmp_path):
    recipe = {"repro": {"@repro": "def test_fine():\n    assert True\n"}, "fix": []}
    store, pipe, _, services = make(live, tmp_path, recipe)
    job = pipe.run(new_job(store).id)
    assert job.state == jobs.FAILED and job.outcome == COULD_NOT_REPRODUCE
    assert (live / "jarvis/audio/speech_text.py").read_text() == SPEECH_TEXT and services.restarts == []


def test_a_fix_that_fails_its_tests_is_never_activated(live, tmp_path):
    bad = {"repro": {"@repro": REPRO},
           "fix": [{"path": "jarvis/audio/speech_text.py", "old": 'return f"{n}%"', "new": 'return f"{n} pct"'}]}
    store, pipe, _, services = make(live, tmp_path, bad)
    job = pipe.run(new_job(store).id)
    assert job.state == jobs.FAILED and job.outcome == TESTS_FAILED
    assert sandbox.head(live) == job.base_commit and services.restarts == []


def test_a_fix_that_breaks_something_else_is_caught_by_the_regression_suite(live, tmp_path):
    breaking = {"repro": {"@repro": REPRO},
                "fix": [{"path": "jarvis/audio/speech_text.py", "old": 'return f"{n}%"',
                         "new": 'return f"{n} percent" if n else None'}]}
    (live / "tests/test_zero.py").write_text("from jarvis.audio.speech_text import percent\n\n"
                                             "def test_zero():\n    assert percent(0) == '0%' or percent(0)\n")
    git(live, "add", "-A")
    git(live, "commit", "-q", "-m", "zero")
    store, pipe, _, _ = make(live, tmp_path, breaking)
    job = pipe.run(new_job(store).id)
    assert job.state == jobs.FAILED and job.outcome == TESTS_FAILED
    assert "tests/test_zero.py::test_zero" in job.last_test("regression after")["failing"]


def test_a_change_outside_the_component_is_discarded(live, tmp_path):
    sneaky = {"repro": {"@repro": REPRO},
              "fix": [{"path": "jarvis/audio/speech_text.py", "old": 'return f"{n}%"', "new": 'return f"{n} percent"'},
                      {"path": "jarvis/integrations/contacts.py", "old": "return name", "new": "return name.lower()"}]}
    store, pipe, _, _ = make(live, tmp_path, sneaky)
    job = pipe.run(new_job(store).id)
    assert job.state == jobs.FAILED and "outside the approved component" in job.outcome
    assert sandbox.head(live) == job.base_commit


def test_a_protected_component_waits_for_a_specific_approval(live, tmp_path):
    recipe = {"repro": {"@repro": "from jarvis.integrations.contacts import resolve\n\n"
                                  "def test_case_insensitive():\n    assert resolve('Papa') == 'papa'\n"},
              "fix": [{"path": "jarvis/integrations/contacts.py", "old": "return name", "new": "return name.lower()"}]}
    store, pipe, said, services = make(live, tmp_path, recipe)
    job = pipe.run(new_job(store, "whatsapp search and contacts").id)
    assert job.state == jobs.AWAITING and job.tier == 2 and job.outcome.startswith(NEEDS_APPROVAL)
    assert "contact resolution" in job.outcome and services.restarts == []
    assert sandbox.head(live) == job.base_commit                         # nothing live yet
    assert pipe.activate(job.id).state == jobs.AWAITING                  # not without approval
    job = pipe.activate(job.id, approved=True)
    assert job.state == jobs.COMPLETED and sandbox.head(live) == job.candidate_commit


class CancellingEditor(Editor):
    def __init__(self, store):
        self.store = store
        self.inner = RecipeEditor(GOOD)

    def write_repro(self, ctx):
        self.inner.write_repro(ctx)

    def write_fix(self, ctx):
        job = self.store.latest()
        self.store.update(job.id, cancel_requested=True)      # "cancel that repair", mid-edit
        self.inner.write_fix(ctx)


def test_cancelling_mid_repair_leaves_the_running_checkout_runnable(live, tmp_path):
    store = JobStore()
    _, pipe, _, services = make(live, tmp_path, editor=CancellingEditor(store))
    pipe.store = store
    job = pipe.run(new_job(store).id)
    assert job.state == jobs.CANCELLED and "Nothing was activated" in job.outcome
    assert sandbox.head(live) == job.base_commit and services.restarts == []
    assert (live / "jarvis/audio/speech_text.py").read_text() == SPEECH_TEXT
    assert not Path(job.worktree).exists()


def test_a_policy_change_during_the_job_stops_it(live, tmp_path, monkeypatch):
    store, pipe, _, _ = make(live, tmp_path, GOOD)
    job = new_job(store)
    store.update(job.id, policy_digest="0" * 16)          # as if the rules were edited after it began
    job = pipe.run(job.id)
    assert job.state == jobs.FAILED and "rules changed" in job.outcome


def test_a_diagnosis_reports_and_changes_nothing(live, tmp_path):
    store, pipe, said, services = make(live, tmp_path, GOOD)
    job = pipe.run(new_job(store, label="diagnostic").id)
    assert job.state == jobs.COMPLETED and job.outcome.startswith("I reproduced it")
    assert sandbox.head(live) == job.base_commit and services.restarts == [] and not job.candidate_commit


def test_without_a_coding_agent_nothing_is_attempted(live, tmp_path):
    store = JobStore()
    pipe = Pipeline(store, live, None, None, python=sys.executable)
    job = pipe.run(new_job(store).id)
    assert job.state == jobs.FAILED and "no coding agent" in job.outcome and not job.worktree
