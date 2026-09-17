"""Iron Man mode must always be escapable and must frame the work, not cover it."""

import asyncio

from jarvis import mode_command
from jarvis.modes import ironman


def test_normal_mode_rescues_the_overlay_even_when_backend_state_was_lost(monkeypatch):
    told = []

    async def tell(shape):
        told.append(shape)

    monkeypatch.setattr(ironman, "_on", None)
    monkeypatch.setattr(mode_command, "_tell_the_overlay", tell)

    assert asyncio.run(mode_command.handle("Jarvis, normal mode")) == ironman.STOOD_DOWN
    assert told == ["pill"]


def test_layout_uses_the_css_frame_dimensions():
    assert ironman._inner_layout(1920, 1080) == {
        "editor": (242, 50, 718, 980),
        "chatgpt": (960, 50, 718, 490),
        "terminal": (960, 540, 718, 490),
    }


def test_layout_launches_missing_windows_and_places_the_real_terminal_title(monkeypatch):
    windows = [("0x1", "Project - Visual Studio Code")]
    launched = []
    placed = []

    monkeypatch.setattr(ironman, "_windows", lambda: list(windows))
    monkeypatch.setattr(ironman, "_screen", lambda: (1000, 500))

    def launch(command):
        launched.append(command)
        if command == ironman.TERMINAL_COMMAND:
            windows.append(("0x2", "xor_sensei@Bhramastra: ~/Madara/Dev/Jarvis"))
        else:
            windows.append(("0x3", "ChatGPT"))
        return True

    monkeypatch.setattr(ironman, "_launch", launch)
    monkeypatch.setattr(ironman, "_place_window",
                        lambda wid, *rect: placed.append((wid, rect)) or True)

    result = ironman.lay_it_out()

    assert result == {"editor": True, "chatgpt": True, "terminal": True}
    assert launched == [ironman.TERMINAL_COMMAND, ironman.CHATGPT_COMMAND]
    assert [wid for wid, _rect in placed] == ["0x1", "0x3", "0x2"]
    assert placed == [
        ("0x1", (126, 23, 374, 454)),
        ("0x3", (500, 23, 374, 227)),
        ("0x2", (500, 250, 374, 227)),
    ]


def test_placement_corrects_xwayland_offset_instead_of_trusting_success(monkeypatch):
    geometry = [484, 100, 718, 980]
    requests = []

    class Done:
        returncode = 0

    def run(command, **_kwargs):
        if "-e" in command:
            sent = [int(value) for value in command[-1].split(",")[1:]]
            requests.append(sent)
            # Reproduce the live HiDPI compositor: it doubles coordinates but not sizes.
            if len(requests) > 1:
                geometry[:] = [242, 50, 718, 980]
        return Done()

    monkeypatch.setattr(ironman.subprocess, "run", run)
    monkeypatch.setattr(ironman, "_window_geometry", lambda _wid: tuple(geometry))
    monkeypatch.setattr(ironman.time, "sleep", lambda _seconds: None)

    assert ironman._place_window("0x1", 242, 50, 718, 980) is True
    assert requests == [[242, 50, 718, 980], [121, 25, 718, 980]]


def test_top_panel_constraint_shortens_window_to_keep_bottom_edge_clear(monkeypatch):
    geometry = [484, 100, 718, 980]
    requests = []

    class Done:
        returncode = 0

    def run(command, **_kwargs):
        if "-e" in command:
            sent = [int(value) for value in command[-1].split(",")[1:]]
            requests.append(sent)
            if len(requests) == 2:
                geometry[:] = [242, 64, 718, 980]
            elif len(requests) == 3:
                geometry[:] = [242, 64, 718, 980]
            elif len(requests) == 4:
                geometry[:] = [242, 64, 718, 966]
        return Done()

    monkeypatch.setattr(ironman.subprocess, "run", run)
    monkeypatch.setattr(ironman, "_window_geometry", lambda _wid: tuple(geometry))
    monkeypatch.setattr(ironman.time, "sleep", lambda _seconds: None)

    assert ironman._place_window("0x1", 242, 50, 718, 980) is True
    assert requests[-1][2:] == [718, 966]
