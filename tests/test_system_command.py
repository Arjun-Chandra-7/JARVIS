"""Volume and brightness must be carried out, or refused for a reason the user can act on."""
import pytest

from jarvis import system_command as sysc


@pytest.mark.parametrize("text,action,kind,value", [
    ("set brightness to 30 percent", "set", "brightness", 30),
    ("set the volume to 40", "set", "volume", 40),
    ("volume 55", "set", "volume", 55),
    ("brightness 20%", "set", "brightness", 20),
    ("set brightness to max", "set", "brightness", 100),
    ("put the volume at half", "set", "volume", 50),
    ("set the keyboard backlight to 50", "set", "keyboard", 50),
])
def test_a_value_is_read_out_of_the_command(text, action, kind, value):
    assert sysc.parse(text) == {"action": action, "kind": kind, "value": value}


@pytest.mark.parametrize("text,kind,delta", [
    ("turn the volume up", "volume", 10),
    ("volume down", "volume", -10),
    ("brightness up a bit", "brightness", 10),
    ("louder", "volume", 10),
    ("quieter", "volume", -10),
])
def test_a_nudge_moves_by_a_step(text, kind, delta):
    assert sysc.parse(text) == {"action": "step", "kind": kind, "value": delta}


@pytest.mark.parametrize("text,kind", [
    ("what is the volume", "volume"),
    ("whats the volume", "volume"),
    ("what is the brightness", "brightness"),
    ("how bright is the screen", "brightness"),
    ("how loud is it", "volume"),
])
def test_a_question_reads_rather_than_sets(text, kind):
    assert sysc.parse(text) == {"action": "read", "kind": kind, "value": None}


@pytest.mark.parametrize("text", [
    "open netflix", "what is my battery", "what time is it", "",
    "set a timer for 5 minutes", "turn on the lights",
])
def test_everything_else_is_left_to_the_model(text):
    assert sysc.parse(text) is None


def test_mute_and_unmute_are_distinguished():
    assert sysc.parse("mute")["value"] is True
    assert sysc.parse("unmute")["value"] is False


# ---------------------------------------------------------------- carrying it out honestly
def test_a_refused_change_explains_itself(monkeypatch):
    """The whole point: a silent no-op must not be reported as done."""
    from jarvis.integrations import system_control as sc

    monkeypatch.setattr(sc, "set_brightness", lambda pct: False)
    monkeypatch.setattr(sc, "get_brightness", lambda: 100)
    monkeypatch.setattr(sc, "brightness_blocker",
                        lambda: "nvidia_0 is owned by the 'video' group. Run usermod -aG video.")

    out = sysc.run({"action": "set", "kind": "brightness", "value": 30})
    assert "video" in out and "30%" not in out


def test_a_change_that_silently_does_nothing_is_caught(monkeypatch):
    """brightnessctl can return success and move nothing; the machine is asked afterwards."""
    from jarvis.integrations import system_control as sc

    monkeypatch.setattr(sc, "set_brightness", lambda pct: True)
    monkeypatch.setattr(sc, "get_brightness", lambda: 100)      # never moves
    monkeypatch.setattr(sc, "brightness_blocker", lambda: "the backlight is not writable.")

    out = sysc.run({"action": "set", "kind": "brightness", "value": 30})
    assert "not writable" in out


def test_a_real_change_reports_the_value_the_machine_gives(monkeypatch):
    from jarvis.integrations import system_control as sc

    state = {"v": "35%"}
    monkeypatch.setattr(sc, "get_volume", lambda: state["v"])

    def _set(pct):
        state["v"] = f"{pct}%"
        return True

    monkeypatch.setattr(sc, "set_volume", _set)
    assert sysc.run({"action": "set", "kind": "volume", "value": 40}) == "Volume is at 40%."


def test_a_nudge_is_applied_to_what_the_machine_reports(monkeypatch):
    from jarvis.integrations import system_control as sc

    state = {"v": "35%"}
    monkeypatch.setattr(sc, "get_volume", lambda: state["v"])

    def _set(pct):
        state["v"] = f"{pct}%"
        return True

    monkeypatch.setattr(sc, "set_volume", _set)
    assert sysc.run({"action": "step", "kind": "volume", "value": 10}) == "Volume is at 45%."


def test_a_nudge_stays_inside_the_range(monkeypatch):
    from jarvis.integrations import system_control as sc

    state = {"v": "97%"}
    monkeypatch.setattr(sc, "get_volume", lambda: state["v"])

    def _set(pct):
        assert pct <= 100
        state["v"] = f"{pct}%"
        return True

    monkeypatch.setattr(sc, "set_volume", _set)
    assert sysc.run({"action": "step", "kind": "volume", "value": 10}) == "Volume is at 100%."
