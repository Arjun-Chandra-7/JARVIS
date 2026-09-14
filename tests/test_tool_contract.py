"""Tool-call validation: repair what is mechanical, refuse to guess what is not.

Every case here is a call a local model actually produced during benchmarking.
"""
import pytest

from jarvis.agent.tool_contract import (
    Outcome,
    ToolResult,
    parse_arguments,
    resolve_name,
    validate,
)


def schema(name, props, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


SET_TIMER = schema("set_timer", {"seconds": {"type": "integer"}}, ["seconds"])
SET_VOLUME = schema("set_volume", {"percent": {"type": "integer"}}, ["percent"])
MEDIA = schema("media_control", {"action": {"type": "string"}}, ["action"])
DND = schema("do_not_disturb", {"on": {"type": "boolean"}}, ["on"])
REMINDER = schema("set_reminder", {"when": {"type": "string"}, "text": {"type": "string"}}, ["when", "text"])


# ----------------------------------------------------------------- safe repairs
def test_string_integer_is_coerced():
    v = validate(SET_TIMER, {"seconds": "300"})
    assert v.ok and v.args == {"seconds": 300}
    assert v.repaired


def test_percent_sign_is_stripped():
    v = validate(SET_VOLUME, {"percent": "30%"})
    assert v.ok and v.args == {"percent": 30}


def test_contained_name_is_renamed():
    """`duration_seconds` unambiguously means `seconds`."""
    v = validate(SET_TIMER, {"duration_seconds": "300"})
    assert v.ok, v.problems
    assert v.args == {"seconds": 300}
    assert any("->" in r for r in v.repaired)


def test_boolean_strings_are_coerced():
    assert validate(DND, {"on": "true"}).args == {"on": True}
    assert validate(DND, {"on": "off"}).args == {"on": False}


def test_float_that_is_whole_becomes_integer():
    v = validate(SET_TIMER, {"seconds": 300.0})
    assert v.ok and v.args == {"seconds": 300}


# ----------------------------------------------------------------- refusals to guess
def test_ambiguous_rename_is_refused():
    """`level` is not `percent`. Renaming it would be inventing intent."""
    v = validate(SET_VOLUME, {"level": "30"})
    assert not v.ok
    assert any("missing required" in p for p in v.problems)


def test_semantically_different_name_is_refused():
    v = validate(MEDIA, {"state": "pause"})
    assert not v.ok
    assert any("action" in p for p in v.problems)


def test_missing_required_is_reported_not_invented():
    v = validate(REMINDER, {"text": "Call Mum at 18:00"})
    assert not v.ok
    assert any("'when'" in p for p in v.problems)
    # ...and the message tells the model exactly what to send next time.
    msg = v.message("set_reminder", REMINDER)
    assert "when" in msg and "set_reminder" in msg


def test_minutes_for_seconds_is_refused():
    """5 != 300. Treating `minutes` as `seconds` would set a five-second timer."""
    v = validate(SET_TIMER, {"minutes": 5})
    assert not v.ok


def test_non_numeric_integer_is_reported():
    v = validate(SET_TIMER, {"seconds": "five"})
    assert not v.ok
    assert any("integer" in p for p in v.problems)


def test_empty_required_value_counts_as_missing():
    assert not validate(SET_TIMER, {"seconds": ""}).ok


def test_no_arguments_when_none_required_is_fine():
    lock = schema("lock_screen", {})
    assert validate(lock, {}).ok
    assert validate(lock, None).ok


# ----------------------------------------------------------------- tool names
def test_exact_name_resolves():
    assert resolve_name("set_timer", ["set_timer", "set_volume"]) == ("set_timer", "")


def test_case_mismatch_resolves():
    got, note = resolve_name("Set_Timer", ["set_timer"])
    assert got == "set_timer" and note


def test_near_miss_resolves():
    got, _ = resolve_name("set_timmer", ["set_timer", "set_volume"])
    assert got == "set_timer"


def test_unknown_name_is_rejected_not_guessed():
    got, note = resolve_name("launch_missiles", ["set_timer", "set_volume"])
    assert got is None and "no tool named" in note


# ----------------------------------------------------------------- argument parsing
def test_parses_plain_json():
    assert parse_arguments('{"seconds": 30}') == ({"seconds": 30}, None)


def test_parses_object_wrapped_in_prose():
    args, err = parse_arguments('Sure! {"seconds": 30}')
    assert err is None and args == {"seconds": 30}


def test_empty_arguments_are_an_empty_dict():
    assert parse_arguments("") == ({}, None)
    assert parse_arguments(None) == ({}, None)


def test_broken_json_is_an_error_not_a_crash():
    args, err = parse_arguments("{seconds: 30")
    assert args == {} and err


def test_json_array_is_rejected():
    args, err = parse_arguments("[1,2,3]")
    assert args == {} and "object" in err


# ----------------------------------------------------------------- outcomes
def test_failure_is_labelled_for_the_model():
    r = ToolResult(Outcome.FAILURE, "device not found", tool="set_volume")
    assert r.for_model().startswith("[failure]")
    assert not r.ok


def test_success_text_is_passed_through_unchanged():
    r = ToolResult(Outcome.SUCCESS, "Volume set to 30%.", tool="set_volume")
    assert r.for_model() == "Volume set to 30%."
    assert r.ok


def test_uncertain_is_distinct_from_failure():
    """An unconfirmed side effect must not look like a failure, or it invites a duplicate retry."""
    r = ToolResult(Outcome.UNCERTAIN, "message may have been sent", tool="whatsapp_send")
    assert not r.ok
    assert r.outcome is Outcome.UNCERTAIN
    assert "uncertain" in r.for_model()


@pytest.mark.parametrize("outcome", list(Outcome))
def test_every_outcome_renders(outcome):
    assert ToolResult(outcome, "x").for_model()
