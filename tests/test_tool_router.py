"""The tool router picks a short, relevant menu instead of shipping all ~80 schemas."""

from __future__ import annotations

import json

import pytest

from jarvis.agent.tool_router import CORE_TOOLS, ToolRouter, _Lexical, _tokens


def _schema(name: str, desc: str, params: dict | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": params or {}, "required": []},
        },
    }


@pytest.fixture
def schemas() -> list[dict]:
    return [
        _schema("run_bash", "Run a shell command on this Linux machine."),
        _schema("recall", "Search the Obsidian memory vault for relevant notes."),
        _schema("system_stats", "Machine health: CPU/mem/GPU/disk/battery/temps."),
        _schema("capture_screen", "Look at the user's screen."),
        _schema("web_search", "Search the web."),
        _schema("log_activity", "Save a short timestamped note to today's journal."),
        _schema("google_agenda", "Upcoming calendar events for N days."),
        _schema("google_email_check", "List Gmail (default unread)."),
        _schema("whatsapp_send", "Send a WhatsApp message to a contact.", {"to": {"type": "string"}}),
        _schema("media_control", "Media: play_pause/next/previous/stop."),
        _schema("set_volume", "Set output volume percent."),
        _schema("lock_screen", "Lock the screen."),
        _schema("bluetooth_scan", "Scan for nearby Bluetooth devices."),
        _schema("who_is_around", "Who is physically nearby right now."),
        _schema("set_timer", "Countdown timer in seconds."),
        _schema("deep_research", "Do serious, up-to-date research via Perplexity."),
        _schema("place_call", "Open the phone dialer for a number."),
        _schema("launch_app", "Launch a desktop app by name."),
    ]


def _router(schemas: list[dict], **kw) -> ToolRouter:
    r = ToolRouter(schemas, **kw)
    r.vectors = {}          # force the lexical-only path: no Ollama in CI
    r._embed_tried = True
    return r


def test_tokens_drop_stopwords_and_short_words():
    assert _tokens("What is my CPU usage right now?") == ["cpu", "usage", "right"]


def test_lexical_ranks_the_obvious_tool_first():
    r = _router
    lex = _Lexical({"system_stats": "cpu memory gpu battery", "lock_screen": "lock the screen"})
    ranked = lex.rank("what is my cpu usage")
    assert ranked and ranked[0][0] == "system_stats"


def test_core_tools_are_always_present(schemas):
    selected = {s["function"]["name"] for s in _router(schemas).select("hello there")}
    for name in CORE_TOOLS:
        assert name in selected


def test_relevant_tool_is_selected(schemas):
    names = _router(schemas).explain("send a whatsapp to mom")
    assert "whatsapp_send" in names


def test_selection_is_capped_and_smaller_than_the_full_set(schemas):
    selected = _router(schemas, k=8).select("play the next song")
    assert len(selected) <= 8
    assert len(selected) < len(schemas)
    assert "media_control" in [s["function"]["name"] for s in selected]


def test_chitchat_falls_back_to_core_only(schemas):
    # No lexical hit and no embeddings -> just the core, not a padded menu of distractors.
    assert _router(schemas).explain("hmm okay") == list(CORE_TOOLS)


def test_extra_pins_a_tool_for_follow_ups(schemas):
    names = _router(schemas).explain("do that again")
    assert "place_call" not in names
    pinned = [s["function"]["name"] for s in _router(schemas).select("do that again", extra=["place_call"])]
    assert "place_call" in pinned


def test_small_tool_sets_are_passed_through_untouched(schemas):
    few = schemas[:5]
    assert _router(few, k=14).select("anything") == few


def test_router_disabled_returns_everything(schemas, monkeypatch):
    monkeypatch.setenv("JARVIS_TOOL_ROUTER", "0")
    assert _router(schemas).select("play the next song") == schemas


def test_routing_cuts_the_schema_payload(schemas):
    full = len(json.dumps(schemas))
    routed = len(json.dumps(_router(schemas).select("what is my cpu usage")))
    assert routed < full / 2


def test_unknown_extra_names_are_ignored(schemas):
    names = _router(schemas).explain("hello")
    pinned = [s["function"]["name"] for s in _router(schemas).select("hello", extra=["no_such_tool"])]
    assert pinned == names
