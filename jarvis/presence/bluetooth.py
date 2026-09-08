"""Bluetooth presence — the best camera-free people sensor on this laptop.

People carry phones, watches and earbuds, all of which advertise over Bluetooth Low
Energy. Reading those advertisements through BlueZ needs no root, does not touch Wi-Fi,
works in the dark and through walls, and gives a real occupancy signal: how many devices
are near, how strong each is, and — for devices bound to a person — who.

Honest limits, stated up front:
* Modern phones rotate a **random** BLE address, so an anonymous device cannot be tracked
  for long and an exact head count is not possible. What is reliable is the *count* of
  nearby devices and its changes, plus the identity of any device you have bound.
* RSSI is proximity, not metres. It is reported as a coarse band (very close / near / in
  the room / far), never a fabricated distance, and never a bearing.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Optional

from . import identity
from .types import Contact, SensorStatus

FRESH_S = float(os.environ.get("JARVIS_BT_FRESH_S", "30"))   # a device unseen this long is gone
POLL_S = 4.0


def rssi_proximity(rssi: Optional[int]) -> str:
    if rssi is None:
        return "unknown"
    if rssi >= -55:
        return "very close"
    if rssi >= -70:
        return "near"
    if rssi >= -85:
        return "in the room"
    return "far"


def rssi_confidence(rssi: Optional[int]) -> float:
    """Higher signal → more sure the device (and its owner) is genuinely present."""
    if rssi is None:
        return 0.4
    # -50 dBm -> ~0.85, -90 dBm -> ~0.35
    return round(max(0.3, min(0.85, 0.85 + (rssi + 50) * 0.0125)), 3)


def _real_name(dev: dict) -> str:
    """A human-meaningful device name, or '' for a fallback like the MAC written out."""
    name = str(dev.get("name") or "").strip()
    plain = dev["address"].replace(":", "").upper()
    if not name or name.replace("-", "").replace(":", "").upper() == plain:
        return ""
    return name


def classify(devices: list[dict], known: dict[str, str], now: float) -> dict:
    """Split fresh devices into bound people, named devices, and an anonymous count.

    Pure, so it is unit-tested. ``people`` are devices bound to a person; ``named`` are
    devices advertising a real name we have not bound (a hint, not a claim); the rest are
    anonymous and only counted.
    """
    fresh = [d for d in devices if now - d.get("last_seen", 0) <= FRESH_S]
    people, named, unknown = [], [], 0
    for d in fresh:
        person = known.get(identity.normalise_mac(d["address"]))
        if person:
            people.append({"person": person, "rssi": d.get("rssi"), "address": d["address"]})
            continue
        nm = _real_name(d)
        if nm:
            named.append({"name": nm, "rssi": d.get("rssi"), "address": d["address"]})
        else:
            unknown += 1
    best: dict[str, dict] = {}
    for p in people:                      # strongest signal per person (they carry several devices)
        cur = best.get(p["person"])
        if cur is None or (p["rssi"] or -999) > (cur["rssi"] or -999):
            best[p["person"]] = p
    named.sort(key=lambda n: -(n["rssi"] or -999))
    return {"people": list(best.values()), "named": named, "unknown": unknown, "total": len(fresh)}


def build_contacts(summary: dict, now: float) -> list[Contact]:
    out = []
    for p in summary["people"]:
        prox = rssi_proximity(p["rssi"])
        out.append(Contact(
            id=f"bt-{identity.normalise_mac(p['address'])}", source="bluetooth",
            distance_m=None, bearing_deg=None, label=p["person"],
            confidence=rssi_confidence(p["rssi"]),
            first_seen=now, last_seen=now,
            detail=f"device {prox}" + (f", {p['rssi']} dBm" if p["rssi"] is not None else "")))
    return out


class BluetoothSensor:
    """Continuously discovers BLE devices via BlueZ and publishes presence contacts."""

    def __init__(self, config=None) -> None:
        self.config = config
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._devices: dict[str, dict] = {}      # address -> {rssi, name, atype, last_seen}
        self._contacts: list[Contact] = []
        self._status = SensorStatus("bluetooth", False, "not started")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="presence-bluetooth", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=4)
        with self._lock:
            self._contacts, self._status = [], SensorStatus("bluetooth", False, "stopped")

    def snapshot(self) -> tuple[list[Contact], SensorStatus]:
        with self._lock:
            return list(self._contacts), self._status

    def _record(self, address: str, changed: dict) -> None:
        if not address:
            return
        now = time.time()
        dev = self._devices.setdefault(address, {"address": address, "rssi": None,
                                                 "name": "", "atype": "", "last_seen": 0})
        if "RSSI" in changed:
            dev["rssi"] = int(changed["RSSI"]); dev["last_seen"] = now
        if "Name" in changed and changed["Name"]:
            dev["name"] = str(changed["Name"])
        if "AddressType" in changed:
            dev["atype"] = str(changed["AddressType"])
        if "Connected" in changed and changed["Connected"]:
            dev["last_seen"] = now

    def _run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._loop())
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._status = SensorStatus("bluetooth", False, f"bluetooth unavailable: {exc}")

    async def _loop(self) -> None:
        from dbus_next import BusType, Message
        from dbus_next.aio import MessageBus

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        # Keep the adapter actively discovering so RSSI keeps updating.
        try:
            intr = await bus.introspect("org.bluez", "/org/bluez/hci0")
            adapter = bus.get_proxy_object("org.bluez", "/org/bluez/hci0", intr).get_interface("org.bluez.Adapter1")
            try:
                await adapter.call_start_discovery()
            except Exception:  # noqa: BLE001 - already discovering is fine
                pass
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._status = SensorStatus("bluetooth", False, f"no Bluetooth adapter: {exc}")
            return

        # Seed from whatever BlueZ already knows.
        try:
            root = await bus.introspect("org.bluez", "/")
            om = bus.get_proxy_object("org.bluez", "/", root).get_interface("org.freedesktop.DBus.ObjectManager")
            for _path, ifaces in (await om.call_get_managed_objects()).items():
                d = ifaces.get("org.bluez.Device1")
                if d and "Address" in d:
                    self._record(d["Address"].value,
                                 {k: v.value for k, v in d.items() if k in ("RSSI", "Name", "AddressType", "Connected")})
        except Exception:  # noqa: BLE001
            pass

        # Live RSSI updates arrive as PropertiesChanged on org.bluez.Device1.
        def on_signal(msg) -> None:
            try:
                if msg.member != "PropertiesChanged" or not str(msg.path).startswith("/org/bluez/hci0/dev_"):
                    return
                iface, changed, _ = msg.body
                if iface != "org.bluez.Device1":
                    return
                address = str(msg.path).split("dev_")[-1].replace("_", ":")
                self._record(address, {k: v.value for k, v in changed.items()})
            except Exception:  # noqa: BLE001
                pass

        bus.add_message_handler(on_signal)
        await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                               interface="org.freedesktop.DBus", member="AddMatch",
                               signature="s",
                               body=["type='signal',interface='org.freedesktop.DBus.Properties',"
                                     "member='PropertiesChanged'"]))

        known = identity.load(self.config) if self.config is not None else {}
        last_known_reload = time.monotonic()
        while not self._stop.is_set():
            await asyncio.sleep(POLL_S)
            now = time.time()
            if time.monotonic() - last_known_reload > 30 and self.config is not None:
                known = identity.load(self.config)
                last_known_reload = time.monotonic()
            summary = classify(list(self._devices.values()), known, now)
            contacts = build_contacts(summary, now)
            detail = (f"{summary['total']} devices near"
                      + (f", {len(summary['people'])} known" if summary["people"] else "")
                      + (f", {len(summary['named'])} named" if summary["named"] else "")
                      + (f", {summary['unknown']} anon" if summary["unknown"] else ""))
            with self._lock:
                self._contacts = contacts
                self._status = SensorStatus("bluetooth", True, detail)
        try:
            await bus.disconnect()
        except Exception:  # noqa: BLE001
            pass


def scan_nearby(config=None) -> dict:
    """A one-off snapshot of currently-visible Bluetooth devices, for listing and binding."""
    svc = sensor(config)
    svc.start()
    time.sleep(0.1)
    with svc._lock:
        pass
    known = identity.load(config) if config is not None else {}
    now = time.time()
    devices = sorted((d for d in svc._devices.values() if now - d.get("last_seen", 0) <= FRESH_S),
                     key=lambda d: -(d.get("rssi") or -999))
    rows = []
    for d in devices:
        rows.append({"address": d["address"], "name": _real_name(d) or "(unnamed)",
                     "rssi": d.get("rssi"), "proximity": rssi_proximity(d.get("rssi")),
                     "person": known.get(identity.normalise_mac(d["address"]))})
    return {"count": len(rows), "devices": rows}


def report(config=None) -> str:
    """Readable list of nearby Bluetooth devices, for a 'scan bluetooth' command."""
    snap = scan_nearby(config)
    if not snap["devices"]:
        return ("No Bluetooth devices seen yet — give it a few seconds after enabling, and make "
                "sure Bluetooth is on. This counts phones/watches/earbuds nearby as a people signal.")
    lines = [f"{snap['count']} Bluetooth device(s) nearby:"]
    for d in snap["devices"]:
        who = f"  [{d['person']}]" if d["person"] else ""
        sig = f"{d['rssi']} dBm" if d["rssi"] is not None else "signal ?"
        lines.append(f"  {d['name']:<22} {d['proximity']:<12} {sig}  {d['address']}{who}")
    lines.append("\nBind one to a person: \"remember that <address> is <name>'s\".")
    return "\n".join(lines)


_sensor: Optional[BluetoothSensor] = None


def sensor(config=None) -> BluetoothSensor:
    global _sensor
    if _sensor is None:
        _sensor = BluetoothSensor(config)
    elif config is not None and _sensor.config is None:
        _sensor.config = config
    return _sensor
