"""Desktop gestures must release input and refuse uncertain visual targets."""

from types import SimpleNamespace

from jarvis.integrations import desktop_control as dc


def test_drag_releases_button_after_move_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(dc, "available", lambda: "ydotool")
    monkeypatch.setattr(dc, "move", lambda x, y: calls.append(("move", x, y)) or len(calls) == 1)
    monkeypatch.setattr(dc, "_button_event", lambda b, down, tool: calls.append(("button", down)) or True)
    monkeypatch.setattr(dc.time, "sleep", lambda _: None)

    assert not dc.drag(10, 20, 30, 40, duration_ms=100)
    assert calls[-1] == ("button", False)


def test_hold_keys_ydotool_releases_in_reverse_order(monkeypatch):
    calls = []
    monkeypatch.setattr(dc, "available", lambda: "ydotool")
    monkeypatch.setattr(dc, "_run", lambda args: calls.append(args) or True)
    monkeypatch.setattr(dc.time, "sleep", lambda _: None)

    assert dc.hold_keys("ctrl+a", 100)
    assert calls == [["ydotool", "key", "29:1", "30:1"],
                     ["ydotool", "key", "30:0", "29:0"]]


def test_visual_click_refuses_low_confidence_and_outside_image(monkeypatch):
    from jarvis.vision import analyze, screenshot

    clicked = []
    monkeypatch.setattr(dc, "available", lambda: "ydotool")
    monkeypatch.setattr(screenshot, "capture", lambda: "/tmp/fake-screen.jpg")
    monkeypatch.setattr(screenshot, "_last_geom", {"img": (100, 100), "real": (200, 200)})
    monkeypatch.setattr(dc, "move_click", lambda *a, **kw: clicked.append(a) or True)
    monkeypatch.setattr(analyze, "verify_point", lambda *a: True)
    monkeypatch.setattr(analyze, "ground", lambda *a: ('{"x": 20, "y": 20, "confidence": 0.3}', "pixel"))

    assert "didn't click" in dc.find_and_click("basket", config=SimpleNamespace())
    monkeypatch.setattr(analyze, "ground", lambda *a: ('{"x": 120, "y": 20, "confidence": 0.9}', "pixel"))
    assert "didn't click" in dc.find_and_click("basket", config=SimpleNamespace())
    assert clicked == []


def test_qwen_normalized_box_maps_to_real_pixels_and_ambiguity_stops(monkeypatch):
    from jarvis.vision import analyze, screenshot

    clicked = []
    monkeypatch.setattr(dc, "available", lambda: "ydotool")
    monkeypatch.setattr(screenshot, "capture", lambda: "/tmp/fake-screen.jpg")
    monkeypatch.setattr(screenshot, "_last_geom", {"img": (100, 100), "real": (200, 200)})
    monkeypatch.setattr(dc, "move_click", lambda *a, **kw: clicked.append(a) or True)
    monkeypatch.setattr(analyze, "verify_point", lambda *a: True)
    box = '{"bbox_2d": [400, 400, 600, 600], "label": "basket"}'
    monkeypatch.setattr(analyze, "ground", lambda *a: (f"[{box}, {box}]", "bbox_1000"))
    assert "didn't click" in dc.find_and_click("basket", config=SimpleNamespace())
    monkeypatch.setattr(analyze, "ground", lambda *a: (f"[{box}]", "bbox_1000"))
    assert "clicked" in dc.find_and_click("basket", config=SimpleNamespace())
    assert clicked == [(100, 100)]


def test_find_and_drag_uses_one_screenshot_and_requires_both_targets(monkeypatch):
    from jarvis.vision import analyze, screenshot

    captures = []
    gestures = []
    monkeypatch.setattr(dc, "available", lambda: "ydotool")
    monkeypatch.setattr(screenshot, "capture", lambda: captures.append(1) or "/tmp/fake-screen.jpg")
    monkeypatch.setattr(screenshot, "_last_geom", {"img": (100, 100), "real": (200, 200)})
    monkeypatch.setattr(dc, "drag", lambda *a, **kw: gestures.append(a) or True)
    monkeypatch.setattr(analyze, "verify_point", lambda *a: True)

    def ground(_, target, __):
        if target == "ball":
            return '[{"bbox_2d": [100, 100, 200, 200], "label": "ball"}]', "bbox_1000"
        return "[]", "bbox_1000"

    monkeypatch.setattr(analyze, "ground", ground)
    assert "didn't act" in dc.find_and_drag("ball", "basket", config=SimpleNamespace())
    assert gestures == []

    monkeypatch.setattr(analyze, "ground", lambda _, target, __: (
        '[{"bbox_2d": [100, 100, 200, 200], "label": "ball"}]' if target == "ball"
        else '[{"bbox_2d": [700, 700, 800, 800], "label": "basket"}]', "bbox_1000"))
    assert "Dragged" in dc.find_and_drag("ball", "basket", config=SimpleNamespace())
    assert captures == [1, 1]
    assert gestures == [(30, 30, 150, 150)]


def test_vision_none_disables_grounding():
    from jarvis.vision import analyze

    config = SimpleNamespace(vision_provider="none", ground_model="qwen3.5:4b")
    assert analyze.available(config) is None
    assert analyze.ground("/tmp/unused.jpg", "basket", config) == (None, "pixel")


def test_visual_click_does_not_send_input_when_crop_check_fails(monkeypatch):
    from jarvis.vision import analyze, screenshot

    clicked = []
    monkeypatch.setattr(dc, "available", lambda: "ydotool")
    monkeypatch.setattr(screenshot, "capture", lambda: "/tmp/fake-screen.jpg")
    monkeypatch.setattr(screenshot, "_last_geom", {"img": (100, 100), "real": (200, 200)})
    monkeypatch.setattr(analyze, "ground", lambda *a: (
        '[{"bbox_2d": [400, 400, 600, 600], "label": "Hustle"}]', "bbox_1000"))
    monkeypatch.setattr(analyze, "verify_point", lambda *a: False)
    monkeypatch.setattr(dc, "move_click", lambda *a, **kw: clicked.append(a) or True)

    assert "didn't click" in dc.find_and_click("Hustle playlist", config=SimpleNamespace())
    assert clicked == []


def test_qwen_bare_box_still_needs_crop_confirmation(monkeypatch):
    from jarvis.vision import analyze, screenshot

    clicked = []
    monkeypatch.setattr(dc, "available", lambda: "ydotool")
    monkeypatch.setattr(screenshot, "capture", lambda: "/tmp/fake-screen.jpg")
    monkeypatch.setattr(screenshot, "_last_geom", {"img": (100, 100), "real": (200, 200)})
    monkeypatch.setattr(analyze, "ground", lambda *a: ("[400,400,600,600]", "bbox_1000"))
    monkeypatch.setattr(analyze, "verify_point", lambda *a: False)
    monkeypatch.setattr(dc, "move_click", lambda *a, **kw: clicked.append(a) or True)

    assert "didn't click" in dc.find_and_click("Spotify icon", config=SimpleNamespace())
    assert clicked == []


def test_crop_label_rejects_neighboring_app_icon():
    from jarvis.vision import analyze

    assert not analyze.matches_verified_label("Spotify icon", "Chrome")
    assert analyze.matches_verified_label("Spotify icon", "Spotify")
    assert not analyze.matches_verified_label("Spotify icon", "Not Spotify; Chrome")
    assert analyze.matches_verified_label("Hustle playlist", "Hustle")
