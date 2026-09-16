"""The call to the brain itself — the one line no test had ever executed.

The specialist work changed how this call is made and every test around it passed, because they
all checked which specialist was chosen and none of them made the call. Live, every turn that
reached this brain answered with:

    [groq error] Completions.create() got multiple values for keyword argument 'temperature'
"""
from __future__ import annotations

import types

import pytest

from jarvis.agent.groq_core import GroqAgent
from jarvis.brains.roster import BY_NAME


class _Recorder:
    """Stands in for the OpenAI-shaped client and records exactly how it was called."""

    def __init__(self):
        self.kwargs = None
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Positional arguments are what made the duplicate possible; insist there are none.
        self.kwargs = kwargs
        message = types.SimpleNamespace(content="ok", tool_calls=None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _core(specialist=None, configured_temperature=0.5):
    core = GroqAgent.__new__(GroqAgent)
    core.client = _Recorder()
    core.model = "llama-3.3-70b"
    core.messages = [{"role": "user", "content": "hello"}]
    core.schemas = []
    core.config = types.SimpleNamespace(temperature=configured_temperature)
    core._specialist = specialist
    core._active_schemas = lambda: []
    return core


def test_the_call_is_actually_makeable():
    """The whole of the bug: this raised TypeError before it ever reached the network."""
    core = _core()
    core._complete()
    assert core.client.kwargs is not None


def test_temperature_is_passed_exactly_once():
    core = _core()
    core._complete()
    assert list(core.client.kwargs).count("temperature") == 1


def test_the_specialist_sets_the_temperature_when_there_is_one():
    """The guardian works at 0.0 whatever the configured default is."""
    core = _core(specialist=BY_NAME["guardian"], configured_temperature=0.9)
    core._complete()
    assert core.client.kwargs["temperature"] == BY_NAME["guardian"].temperature


def test_the_configured_temperature_is_used_when_there_is_no_specialist():
    core = _core(specialist=None, configured_temperature=0.42)
    core._complete()
    assert core.client.kwargs["temperature"] == 0.42


def test_a_specialist_without_its_own_model_uses_the_default_one():
    core = _core(specialist=BY_NAME["companion"])
    core._complete()
    assert core.client.kwargs["model"] == "llama-3.3-70b"


@pytest.mark.parametrize("name", sorted(BY_NAME))
def test_every_specialist_can_make_the_call(name):
    """Fifteen of them, each with its own temperature and possibly its own model. A roster entry
    that cannot be used is a roster entry that has not been tried."""
    core = _core(specialist=BY_NAME[name])
    core._complete()
    assert core.client.kwargs["temperature"] == BY_NAME[name].temperature
    assert core.client.kwargs["model"]


# --------------------------------------------------------- one specialist at a time, not all of them
from jarvis.agent.groq_core import TURN_NOTE, _is_a_turn_note  # noqa: E402


def _notes(messages):
    return [m for m in messages if _is_a_turn_note(m)]


def test_a_turn_note_is_told_apart_from_the_standing_prompt():
    assert _is_a_turn_note({"role": "system", "content": f"{TURN_NOTE} You write code."})
    assert not _is_a_turn_note({"role": "system", "content": "You are JARVIS, assistant to..."})
    assert not _is_a_turn_note({"role": "user", "content": "draw me a fox"})


def test_last_turns_instruction_is_removed_before_this_turns_is_added():
    """Three turns in, the model was being told it was a coder, a scribe and a companion at
    once, with the oldest instruction sitting closest to the standing prompt."""
    messages = [
        {"role": "system", "content": "You are JARVIS."},
        {"role": "system", "content": f"{TURN_NOTE} You write and debug code."},
        {"role": "user", "content": "why is this throwing"},
        {"role": "assistant", "content": "because x"},
    ]
    kept = [m for m in messages if not _is_a_turn_note(m)]
    kept.append({"role": "system", "content": f"{TURN_NOTE} You are good company."})
    assert len(_notes(kept)) == 1
    assert "good company" in _notes(kept)[0]["content"]


def test_the_standing_prompt_always_survives():
    """It says who the user is and what this machine is — true whichever specialist is working."""
    messages = [
        {"role": "system", "content": "You are JARVIS."},
        {"role": "system", "content": f"{TURN_NOTE} You make images."},
    ]
    kept = [m for m in messages if not _is_a_turn_note(m)]
    assert kept == [{"role": "system", "content": "You are JARVIS."}]


def test_many_turns_never_accumulate_more_than_one_note():
    from jarvis.brains.roster import BY_NAME
    messages = [{"role": "system", "content": "You are JARVIS."}]
    for name in ("coder", "scribe", "companion", "artist", "guardian"):
        messages = [m for m in messages if not _is_a_turn_note(m)]
        messages.append({"role": "system",
                         "content": f"{TURN_NOTE} {BY_NAME[name].instruction}"})
        messages.append({"role": "user", "content": "..."})
        assert len(_notes(messages)) == 1
