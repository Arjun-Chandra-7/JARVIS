"""The presence service: run the enabled sensors, fuse them, publish one snapshot.

Which sensors run is opt-in through ``JARVIS_PRESENCE`` (comma separated):

    audio     passive listening — "someone is here and talking". No direction, no sound
              emitted, works in the dark and with the lid shut. Reliable.
    network   Bluetooth/Wi-Fi device bindings — names, no position. Cheap, private.
    camera    webcam face detection — the ONLY source of bearing. Lights the webcam LED
              and needs light and line of sight.
    acoustic  near-ultrasound ranging. OFF by default and not recommended: measured on
              this hardware it cannot separate a moving person from room reverberation,
              and it holds the speaker continuously.

Default is ``audio,network`` — the two that work with no camera and no line of sight.
Add ``camera`` to get bearing and metric range.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from .tracker import Tracker
from .types import Contact, SensorStatus, Snapshot

DEFAULT_SENSORS = "audio,network"
FUSE_HZ = 4.0


def enabled_sensors() -> set[str]:
    raw = os.environ.get("JARVIS_PRESENCE", DEFAULT_SENSORS)
    if raw.strip().lower() in {"0", "off", "none", "false"}:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


class PresenceService:
    def __init__(self, config=None) -> None:
        self.config = config
        self._tracker = Tracker()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot = Snapshot(sensors=[SensorStatus("presence", False, "not started")])
        self._sensors = enabled_sensors()
        self._camera = None
        self._acoustic = None
        self._passive = None
        self._net_at = 0.0
        self._net_cache: tuple[list[Contact], SensorStatus] = ([], SensorStatus("network", False, "not scanned"))

    # --- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if "camera" in self._sensors:
            from . import camera
            self._camera = camera.sensor()
            self._camera.start()
        if "acoustic" in self._sensors:
            from . import acoustic
            self._acoustic = acoustic.sensor()
            self._acoustic.start()
        if "audio" in self._sensors:
            from . import passive
            self._passive = passive.sensor()
            self._passive.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="presence-fusion", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for sensor in (self._camera, self._acoustic, self._passive):
            if sensor is not None:
                sensor.stop()
        if self._thread:
            self._thread.join(timeout=4)

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    # --- fusion loop -----------------------------------------------------------
    def _network(self, now: float) -> tuple[list[Contact], SensorStatus]:
        """Scanning radios is slow and power-hungry, so refresh it far less often."""
        if self.config is None:
            return [], SensorStatus("network", False, "no config")
        if now - self._net_at > 20.0:
            self._net_at = now
            try:
                from . import identity
                self._net_cache = identity.contacts(self.config)
            except Exception as exc:  # noqa: BLE001
                self._net_cache = ([], SensorStatus("network", False, str(exc)))
        return self._net_cache

    def _run(self) -> None:
        period = 1.0 / FUSE_HZ
        while not self._stop.is_set():
            started = time.monotonic()
            now = time.time()
            contacts: list[Contact] = []
            statuses: list[SensorStatus] = []

            if self._camera is not None:
                found, status = self._camera.snapshot()
                contacts += found
                statuses.append(status)
            if self._acoustic is not None:
                found, status = self._acoustic.snapshot()
                contacts += found
                statuses.append(status)
            if self._passive is not None:
                found, status = self._passive.snapshot()
                contacts += found
                statuses.append(status)
            if "network" in self._sensors:
                found, status = self._network(now)
                contacts += found
                statuses.append(status)
            if not statuses:
                statuses = [SensorStatus("presence", False, "no sensors enabled (set JARVIS_PRESENCE)")]

            snap = self._tracker.update(contacts, statuses, now=now)
            with self._lock:
                self._snapshot = snap
            time.sleep(max(0.0, period - (time.monotonic() - started)))


_service: Optional[PresenceService] = None


def service(config=None) -> PresenceService:
    global _service
    if _service is None:
        _service = PresenceService(config)
    elif config is not None and _service.config is None:
        _service.config = config
    return _service


def start(config=None) -> PresenceService:
    svc = service(config)
    svc.start()
    return svc


def snapshot(config=None) -> dict:
    return service(config).snapshot().to_dict()


def summary(config=None) -> str:
    """One spoken sentence describing who is around — honest about what is unknown."""
    snap = service(config).snapshot()
    located = [c for c in snap.contacts if c.bearing_deg is not None and c.confidence >= 0.5]
    named = [c.label for c in snap.contacts if c.label]
    echoes = [c for c in snap.contacts if c.source.startswith("acoustic") and c.bearing_deg is None]

    if not snap.contacts:
        blocked = [s for s in snap.sensors if not s.ok]
        if blocked and not any(s.ok for s in snap.sensors):
            return f"I can't see anyone, sir — {blocked[0].detail}."
        return "Nobody in view, sir."

    parts = []
    if located:
        who = []
        for c in located:
            side = "ahead" if abs(c.bearing_deg) < 12 else ("to your right" if c.bearing_deg > 0 else "to your left")
            who.append(f"{c.label or 'someone'} about {c.distance_m:.1f} metres {side}")
        parts.append(("One person: " if len(who) == 1 else f"{len(who)} people: ") + ", ".join(who))
    if named and not located:
        parts.append(", ".join(sorted(set(named))) + " nearby by device")
    heard = [c for c in snap.contacts if c.source.startswith("audio")]
    if heard and not located:
        parts.append("I can hear someone talking, but I can't tell where from")
    if echoes:
        parts.append(f"{len(echoes)} moving contact{'s' if len(echoes) > 1 else ''} at "
                     + ", ".join(f"{c.distance_m:.1f} m" for c in echoes) + ", bearing unknown")
    return ". ".join(parts) + "."
