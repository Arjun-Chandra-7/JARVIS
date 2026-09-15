"""Visible clicks must dispatch to the native screen targeter and report actual input."""

import asyncio

import pytest

from jarvis import screen_command
from jarvis.integrations import accessibility, desktop_control


@pytest.mark.parametrize("spoken,target", [
    ("Click the Hustle playlist", "Hustle playlist"),
    ("click on the Hustle playlist on my screen", "Hustle playlist"),
    ("Now open my hostel playlist on my screen right now", "hostel playlist"),
    ("Use your agentic DOM and open the hustle playlist on my screen, click on it", "hustle playlist"),
])
def test_screen_target_extraction(spoken, target):
    assert screen_command.target_from(spoken) == target


def test_open_app_and_vague_it_do_not_become_screen_targets():
    assert screen_command.target_from("Open Spotify") is None
    assert screen_command.target_from("Click on it") is None


def test_screen_command_dispatches_actual_target(monkeypatch):
    called = []
    monkeypatch.setattr(desktop_control, "click_target", lambda target, button, double, config:
                        called.append((target, button, double)) or "Clicked the visible Hustle control.")
    assert asyncio.run(screen_command.handle("Click the Hustle playlist", object())).startswith("Clicked")
    assert called == [("Hustle playlist", "left", False)]


def test_accessibility_exact_target_and_ambiguity():
    node = {"name": "Hustle", "role": "list item", "rect": [30, 40, 80, 24], "actions": ["click"]}
    chosen, reason = accessibility.choose([node], "Hustle playlist")
    assert chosen == node and not reason
    other = {**node, "rect": [30, 120, 80, 24]}
    chosen, reason = accessibility.choose([node, other], "Hustle playlist")
    assert chosen is None and "Several" in reason


def test_click_target_uses_native_bounds_before_vision(monkeypatch):
    clicked = []
    node = {"name": "Hustle", "role": "list item", "rect": [30, 40, 80, 24], "actions": ["click"]}
    monkeypatch.setattr(desktop_control, "available", lambda: "ydotool")
    monkeypatch.setattr(accessibility, "snapshot", lambda: {"ok": True, "app": "Spotify", "nodes": [node]})
    monkeypatch.setattr(desktop_control, "move_click", lambda *a: clicked.append(a) or True)
    monkeypatch.setattr(desktop_control, "find_and_click", lambda *a: pytest.fail("vision fallback ran"))
    result = desktop_control.click_target("Hustle playlist")
    assert "Clicked" in result and "Spotify" in result
    assert clicked == [(70, 52, "left", False)]


def test_commands_layer_uses_screen_click_before_open_parser(monkeypatch):
    from jarvis import commands, open_command, system_command

    monkeypatch.setattr(system_command, "handle", lambda *a: asyncio.sleep(0, result=None))
    monkeypatch.setattr(screen_command, "handle", lambda *a: asyncio.sleep(0, result="Clicked Hustle."))
    monkeypatch.setattr(open_command, "handle", lambda *a: pytest.fail("open parser ran"))
    assert asyncio.run(commands.handle("Open the Hustle playlist on my screen", object())) == "Clicked Hustle."
