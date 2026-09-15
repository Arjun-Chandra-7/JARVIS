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


# --------------------------------------------- choosing a control when Wayland hides positions
from jarvis.integrations import accessibility  # noqa: E402


def _node(name, role="button", path=(0,), actions=("click",), rect=(0, 0, 64, 44)):
    return {"name": name, "role": role, "path": list(path),
            "actions": list(actions), "rect": list(rect)}


def test_a_control_is_found_although_every_position_is_zero():
    """Wayland never tells a client where its window is, so AT-SPI reports (0, 0) for everything.
    Verified on this machine: all sixty-two gnome-calculator controls came back [0, 0, w, h]."""
    node, why = accessibility.choose([_node("7", path=(0, 1)), _node("8", path=(0, 2))], "8")
    assert node is not None and node["name"] == "8", why


def test_symbol_controls_are_reachable():
    """Names were tokenised with [\\w']+, which finds nothing in these, so every symbol control
    on screen was invisible to the matcher and fell through to vision."""
    nodes = [_node(s, path=(0, i)) for i, s in enumerate(["×", "=", "−", "→", "✓"])]
    for symbol in ("×", "=", "−", "→", "✓"):
        node, why = accessibility.choose(nodes, symbol)
        assert node is not None and node["name"] == symbol, why


def test_the_button_wins_over_a_label_saying_the_same_thing():
    """GTK exposes both; only one of them is the thing to press."""
    label = _node("7", role="label", path=(0, 1, 0),
                  actions=("clipboard.copy", "menu.popup"))
    button = _node("7", role="button", path=(0, 1), actions=("click",))
    node, _ = accessibility.choose([label, button], "7")
    assert node is button


def test_two_different_controls_with_one_name_are_refused():
    """The old guard compared screen centres. With every centre at (0, 0) two different buttons
    of the same size looked like one place, and it would quietly press one of them."""
    a = _node("Allow", path=(0, 3))
    b = _node("Allow", path=(0, 9))
    node, why = accessibility.choose([a, b], "Allow")
    assert node is None
    assert "won't guess" in why


def test_the_same_control_seen_twice_is_not_an_ambiguity():
    same = _node("Allow", path=(0, 3))
    node, _ = accessibility.choose([same, dict(same)], "Allow")
    assert node is not None


def test_an_unknown_name_is_left_for_vision():
    node, why = accessibility.choose([_node("Cancel")], "Allow")
    assert node is None and "isn't in the active app" in why
