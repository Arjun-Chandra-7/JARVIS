"""Who is around, from the devices they carry.

Bluetooth and Wi-Fi/LAN give no position at all, but they give the one thing the
camera and the sonar cannot: a *name*. A phone seen on the LAN means its owner is
almost certainly in the building even when no camera can see them.

Bindings live in the vault's private area (git-excluded) as {mac: person}. They are
learned explicitly — "that phone is Maya's" — never inferred, so a track is never
labelled with a guess.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from .types import Contact, SensorStatus

# Randomised/locally-administered MACs change constantly and cannot identify anyone.
_LOCALLY_ADMINISTERED_BIT = 0x02


def _path(config) -> Path:
    return Path(config.vault_path) / "Jarvis" / "private" / "known-devices.json"


def normalise_mac(mac: str) -> str:
    return (mac or "").strip().lower().replace("-", ":")


def is_randomised(mac: str) -> bool:
    """True for a privacy-randomised MAC, which must never be bound to a person."""
    mac = normalise_mac(mac)
    parts = mac.split(":")
    if len(parts) != 6:
        return True
    try:
        return bool(int(parts[0], 16) & _LOCALLY_ADMINISTERED_BIT)
    except ValueError:
        return True


def load(config) -> dict[str, str]:
    try:
        data = json.loads(_path(config).read_text(encoding="utf-8"))
        return {normalise_mac(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(config, mac: str, person: str) -> dict[str, Any]:
    """Bind one device to one person. Refuses randomised MACs, which are meaningless."""
    mac, person = normalise_mac(mac), (person or "").strip()
    if not mac or not person:
        return {"ok": False, "message": "Need both a MAC address and a name."}
    if is_randomised(mac):
        return {"ok": False, "message": f"{mac} is a randomised (private) address — it will "
                                        "change, so binding it to a person would be useless."}
    known = load(config)
    known[mac] = person
    target = _path(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(known, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(target)
    return {"ok": True, "message": f"Noted — {mac} is {person}'s."}


def forget(config, mac: str) -> bool:
    known = load(config)
    if known.pop(normalise_mac(mac), None) is None:
        return False
    _path(config).write_text(json.dumps(known, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def _devices(snapshot: dict) -> list[tuple[str, str, str]]:
    """Flatten a nearby.snapshot() into (mac, name, channel) triples."""
    out: list[tuple[str, str, str]] = []
    for channel in ("bt", "lan", "wifi"):
        for row in snapshot.get(channel) or []:
            if not isinstance(row, dict):
                continue
            mac = normalise_mac(row.get("mac") or row.get("address") or row.get("bssid") or "")
            if mac:
                out.append((mac, str(row.get("name") or row.get("host") or ""), channel))
    return out


def contacts(config, snapshot: Optional[dict] = None) -> tuple[list[Contact], SensorStatus]:
    """Named people implied by the devices currently visible. No position, ever."""
    if snapshot is None:
        try:
            from .. import nearby
            snapshot = nearby.snapshot()
        except Exception as exc:  # noqa: BLE001
            return [], SensorStatus("network", False, f"scan unavailable: {exc}")

    known = load(config)
    seen = _devices(snapshot)
    now = time.time()
    by_person: dict[str, list[str]] = {}
    for mac, _name, channel in seen:
        person = known.get(mac)
        if person:
            by_person.setdefault(person, []).append(channel)

    out = [
        Contact(
            id=f"net-{person}",
            source="network",
            distance_m=None,
            bearing_deg=None,          # a MAC address has no direction
            confidence=0.7,
            label=person,
            first_seen=now, last_seen=now,
            detail="device seen on " + "/".join(sorted(set(channels))),
        )
        for person, channels in sorted(by_person.items())
    ]
    detail = f"{len(seen)} devices, {len(known)} bound, {len(out)} known people"
    return out, SensorStatus("network", bool(seen), detail)
