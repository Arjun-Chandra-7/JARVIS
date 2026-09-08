"""Bluetooth presence: RSSI banding, device classification, and honest counting."""
import time

from jarvis.presence import bluetooth as bt
from jarvis.presence import identity


def dev(addr, rssi=-70, name="", age=0.0):
    return {"address": addr, "rssi": rssi, "name": name, "last_seen": time.time() - age}


def test_rssi_proximity_bands():
    assert bt.rssi_proximity(-50) == "very close"
    assert bt.rssi_proximity(-65) == "near"
    assert bt.rssi_proximity(-80) == "in the room"
    assert bt.rssi_proximity(-95) == "far"
    assert bt.rssi_proximity(None) == "unknown"


def test_rssi_confidence_rises_with_signal():
    assert bt.rssi_confidence(-50) > bt.rssi_confidence(-90)
    assert 0.3 <= bt.rssi_confidence(-90) <= bt.rssi_confidence(-50) <= 0.85


def test_stale_devices_are_dropped():
    now = time.time()
    devices = [dev("AA:BB:CC:DD:EE:01", age=0), dev("AA:BB:CC:DD:EE:02", age=bt.FRESH_S + 5)]
    summ = bt.classify(devices, {}, now)
    assert summ["total"] == 1


def test_bound_device_becomes_a_named_person():
    now = time.time()
    known = {"aa:bb:cc:dd:ee:01": "Maya"}
    summ = bt.classify([dev("AA:BB:CC:DD:EE:01", rssi=-55)], known, now)
    assert summ["people"] and summ["people"][0]["person"] == "Maya"
    contacts = bt.build_contacts(summ, now)
    assert len(contacts) == 1
    c = contacts[0]
    assert c.label == "Maya" and c.source == "bluetooth"
    assert c.distance_m is None and c.bearing_deg is None      # no position, ever
    assert "very close" in c.detail


def test_named_but_unbound_is_a_hint_not_a_person():
    now = time.time()
    summ = bt.classify([dev("2C:9C:58:00:00:01", name="GoogleTV6607")], {}, now)
    assert summ["people"] == [] and summ["named"][0]["name"] == "GoogleTV6607"
    assert bt.build_contacts(summ, now) == []                  # a TV is not a person contact


def test_fallback_name_equal_to_mac_is_treated_as_anonymous():
    now = time.time()
    summ = bt.classify([dev("52:41:F8:24:00:CE", name="52-41-F8-24-00-CE")], {}, now)
    assert summ["named"] == [] and summ["unknown"] == 1


def test_multiple_devices_for_one_person_collapse_to_strongest():
    now = time.time()
    known = {"aa:bb:cc:dd:ee:01": "Maya", "aa:bb:cc:dd:ee:02": "Maya"}
    summ = bt.classify([dev("AA:BB:CC:DD:EE:01", rssi=-80),
                        dev("AA:BB:CC:DD:EE:02", rssi=-55)], known, now)
    assert len(summ["people"]) == 1 and summ["people"][0]["rssi"] == -55


def test_anonymous_devices_are_only_counted():
    now = time.time()
    summ = bt.classify([dev("D3:EA:89:00:00:01"), dev("52:41:F8:00:00:02")], {}, now)
    assert summ["unknown"] == 2 and summ["people"] == [] and summ["named"] == []
