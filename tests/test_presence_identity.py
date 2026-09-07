"""Device-to-person bindings: identity without position, and no guessing."""
from types import SimpleNamespace

import pytest

from jarvis.presence import identity


def cfg(tmp_path):
    return SimpleNamespace(vault_path=tmp_path)


def test_randomised_macs_are_rejected():
    # bit 0x02 of the first octet = locally administered = privacy-randomised
    assert identity.is_randomised("a2:11:22:33:44:55")
    assert identity.is_randomised("not-a-mac")
    assert not identity.is_randomised("A0:11:22:33:44:55")


def test_remember_refuses_a_randomised_address(tmp_path):
    result = identity.remember(cfg(tmp_path), "a2:11:22:33:44:55", "Maya")
    assert result["ok"] is False and "randomised" in result["message"]
    assert identity.load(cfg(tmp_path)) == {}


def test_remember_and_forget_roundtrip(tmp_path):
    config = cfg(tmp_path)
    assert identity.remember(config, "A0:11:22:33:44:55", "Maya")["ok"]
    assert identity.load(config) == {"a0:11:22:33:44:55": "Maya"}
    assert (tmp_path / "Jarvis/private/known-devices.json").exists()
    assert identity.forget(config, "a0:11:22:33:44:55") is True
    assert identity.forget(config, "a0:11:22:33:44:55") is False


def test_remember_needs_both_fields(tmp_path):
    assert not identity.remember(cfg(tmp_path), "", "Maya")["ok"]
    assert not identity.remember(cfg(tmp_path), "A0:11:22:33:44:55", "  ")["ok"]


def test_contacts_are_identity_only_never_positioned(tmp_path):
    config = cfg(tmp_path)
    identity.remember(config, "A0:11:22:33:44:55", "Maya")
    snap = {"bt": [{"mac": "A0:11:22:33:44:55", "name": "Pixel"}],
            "lan": [{"mac": "a0:11:22:33:44:55"}],
            "wifi": [{"mac": "BB:CC:DD:EE:FF:00"}]}
    contacts, status = identity.contacts(config, snap)
    assert len(contacts) == 1
    c = contacts[0]
    assert c.label == "Maya" and c.source == "network"
    assert c.distance_m is None and c.bearing_deg is None
    assert c.position() is None
    assert "bt" in c.detail and "lan" in c.detail        # both channels merged into one person
    assert status.ok and "1 known people" in status.detail


def test_unknown_devices_produce_no_contacts(tmp_path):
    contacts, status = identity.contacts(cfg(tmp_path), {"bt": [{"mac": "A0:00:00:00:00:01"}]})
    assert contacts == []
    assert status.ok is True                             # scanning worked, just nobody known


def test_empty_scan_reports_not_ok(tmp_path):
    contacts, status = identity.contacts(cfg(tmp_path), {"bt": [], "lan": [], "wifi": []})
    assert contacts == [] and status.ok is False


@pytest.mark.parametrize("raw,expected", [
    ("A0-11-22-33-44-55", "a0:11:22:33:44:55"),
    ("  a0:11:22:33:44:55 ", "a0:11:22:33:44:55"),
])
def test_mac_normalisation(raw, expected):
    assert identity.normalise_mac(raw) == expected
