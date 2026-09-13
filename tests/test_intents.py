"""The deterministic command path: what it must catch, and what it must refuse to catch."""

from __future__ import annotations

import pytest

from jarvis.intents.grammar import compile_template, match, normalise, parse_duration, parse_number
from jarvis.intents.router import IntentRouter, parse_intent_file


# --- number and duration parsing ----------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("7", 7), ("42", 42), ("3.5", 3.5),
    ("seven", 7), ("twenty", 20), ("twenty five", 25), ("twenty-five", 25),
    ("a hundred", 100), ("two hundred", 200),
    ("half", 0.5), ("quarter", 0.25),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("text", ["", "banana", "the quick brown fox", None])
def test_parse_number_rejects_non_numbers(text):
    assert parse_number(text) is None


@pytest.mark.parametrize("text,seconds", [
    ("10 minutes", 600),
    ("ten minutes", 600),
    ("90 seconds", 90),
    ("2 hours", 7200),
    ("1 hour 30", 5400),
    ("an hour and a half", 5400),
    ("half an hour", 1800),
    ("a quarter of an hour", 900),
    ("25 mins", 1500),
    ("5", 300),                      # a bare number is minutes, which is how people speak
])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["banana minutes", "", "soon", "later today"])
def test_parse_duration_rejects_nonsense(text):
    assert parse_duration(text) is None


# --- template compilation -------------------------------------------------------------------
def test_alternatives_and_optionals():
    c = compile_template("[set|start] a timer [now]")
    assert match(c, "set a timer") == {}
    assert match(c, "start a timer now") == {}
    assert match(c, "a timer") == {}
    assert match(c, "set a kettle") is None


def test_captures_become_arguments():
    c = compile_template("volume {percent:percent}")
    assert match(c, "volume 40") == {"percent": 40}


def test_percent_is_clamped():
    c = compile_template("volume {percent:percent}")
    assert match(c, "volume 900") == {"percent": 100}


def test_a_capture_that_cannot_convert_is_not_a_match():
    c = compile_template("timer for {seconds:duration}")
    assert match(c, "timer for banana minutes") is None


def test_matching_is_anchored():
    # The important safety property: a template must fit the whole utterance.
    c = compile_template("[set] a timer for {seconds:duration}")
    assert match(c, "do not set a timer for 10 minutes") is None
    assert match(c, "set a timer for 10 minutes and call mum") is None


def test_slots_come_from_live_data():
    c = compile_template("message $contacts", {"contacts": ["Priya", "Anish Chandra"]})
    assert match(c, "message anish chandra") == {"contacts": "anish chandra"}
    assert match(c, "message someone else") is None


def test_an_unfilled_slot_matches_nothing_rather_than_everything():
    c = compile_template("message $contacts", {"contacts": []})
    assert match(c, "message anyone") is None


def test_normalise_strips_wake_word_and_filler():
    assert normalise("Hey Jarvis, could you lock the screen?") == "lock the screen"
    assert normalise("  OK Jarvis   volume  40 ") == "volume 40"


# --- intent files -------------------------------------------------------------------------------
def test_parse_intent_file():
    rules = parse_intent_file("""
        # a comment
        [set_timer]
        timer for {seconds:duration}
        [lock_screen]
        lock it
    """)
    assert [(r.tool, r.template) for r in rules] == [
        ("set_timer", "timer for {seconds:duration}"),
        ("lock_screen", "lock it"),
    ]


def test_templates_before_a_header_are_ignored():
    assert parse_intent_file("stray template\n[t]\nreal one") == parse_intent_file("[t]\nreal one")


# --- the router ---------------------------------------------------------------------------------
@pytest.fixture
def router():
    return IntentRouter()


@pytest.mark.parametrize("utterance,tool", [
    ("set a timer for 10 minutes", "set_timer"),
    ("start a timer for an hour and a half", "set_timer"),
    ("wake me up in 25 minutes", "set_timer"),
    ("set the volume to 40 percent", "set_volume"),
    ("volume 70", "set_volume"),
    ("lock the screen", "lock_screen"),
    ("brightness 30", "set_brightness"),
    ("what am i doing right now", "what_am_i_doing"),
    ("who is around", "who_is_around"),
])
def test_rote_commands_resolve(router, utterance, tool):
    hit = router.resolve(utterance)
    assert hit is not None, f"{utterance!r} should have matched"
    assert hit.tool == tool


def test_a_question_mark_does_not_prevent_a_match(router):
    assert router.resolve("who is around?") is not None


@pytest.mark.parametrize("utterance", [
    "don't set a timer for 10 minutes",
    "what is the capital of France?",
    "can you explain how timers work in python",
    "set a timer for banana minutes",
    "message Priya that I'll be late and tell her the meeting moved to Thursday",
    "",
])
def test_everything_else_falls_through_to_the_model(router, utterance):
    assert router.resolve(utterance) is None


def test_long_utterances_are_left_to_the_model(router):
    # A sentence this long is a request, not a command, whatever it happens to contain.
    assert router.resolve("set a timer for 10 minutes " + "and then also " * 6) is None


def test_arguments_are_passed_as_strings(router):
    # Tools take the loose string arguments a model would send, so both paths behave identically.
    hit = router.resolve("set a timer for 10 minutes")
    assert hit.args == {"seconds": "600"}


def test_the_router_can_be_switched_off(router, monkeypatch):
    monkeypatch.setenv("JARVIS_INTENTS", "0")
    assert router.resolve("lock the screen") is None


def test_every_shipped_rule_names_a_real_tool():
    from jarvis.tools import base

    base.load_domains()
    known = set(base.REGISTRY.names())
    unknown = sorted({r.tool for r in IntentRouter().rules} - known)
    assert unknown == [], f".intent files reference tools that do not exist: {unknown}"


def test_every_capture_is_a_real_argument_of_its_tool():
    """A typo in a capture name would silently pass an argument the tool ignores."""
    from jarvis.tools import base

    base.load_domains()
    problems = []
    for rule in IntentRouter().rules:
        spec = base.REGISTRY.get(rule.tool)
        if spec is None:
            continue
        allowed = set(spec.params)
        for capture in rule.compiled.converters:
            if capture not in allowed:
                problems.append(f"{rule.tool}: {rule.template!r} captures {capture!r}, "
                                f"but the tool takes {sorted(allowed)}")
    assert problems == [], "\n".join(problems)
