"""Charger announcements fire on transitions only, and say the right thing."""
import pytest

from jarvis.integrations import power_supply as ps


def state(plugged=True, percent=76, status="Charging", present=True):
    return {"present": present, "plugged": plugged, "percent": percent, "status": status}


def test_real_read_has_the_expected_shape():
    data = ps.read()
    assert set(data) == {"present", "plugged", "percent", "status"}
    assert isinstance(data["plugged"], bool)


def test_spoken_state_wordings():
    assert ps.spoken_state(state(True, 76, "Charging")) == "charging, battery 76 percent"
    assert ps.spoken_state(state(True, 100, "Full")) == "charged, battery 100 percent"
    assert ps.spoken_state(state(False, 41, "Discharging")) == "on battery, 41 percent"
    assert ps.spoken_state(state(present=False)) == "no battery detected"
    assert "unknown charge" in ps.spoken_state(state(True, None, "Charging"))


def test_full_at_100_reads_as_charged_even_if_status_lags():
    assert "charged" in ps.spoken_state(state(True, 100, "Charging"))


def test_first_poll_is_a_baseline_not_an_announcement():
    w = ps.PowerWatcher()
    assert w.poll(state(plugged=True)) is None


def test_announces_on_plug_in_with_percentage():
    w = ps.PowerWatcher()
    w.poll(state(plugged=False, percent=61, status="Discharging"))
    msg = w.poll(state(plugged=True, percent=61, status="Charging"))
    assert msg == "Laptop charging, battery 61 percent."


def test_announces_on_unplug():
    w = ps.PowerWatcher()
    w.poll(state(plugged=True))
    msg = w.poll(state(plugged=False, percent=58, status="Discharging"))
    assert msg == "Charger unplugged, sir. Battery 58 percent."


def test_plugging_in_when_already_full_says_so():
    w = ps.PowerWatcher()
    w.poll(state(plugged=False, percent=100, status="Not charging"))
    msg = w.poll(state(plugged=True, percent=100, status="Full"))
    assert "already full" in msg and "100 percent" in msg


def test_no_repeat_while_the_state_holds():
    w = ps.PowerWatcher()
    w.poll(state(plugged=False))
    assert w.poll(state(plugged=True)) is not None
    for _ in range(5):
        assert w.poll(state(plugged=True)) is None       # silent until it changes again


def test_machines_without_a_battery_never_announce():
    w = ps.PowerWatcher()
    assert w.poll(state(present=False)) is None
    assert w.poll(state(present=False, plugged=False)) is None
