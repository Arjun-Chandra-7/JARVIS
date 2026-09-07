"""Fuse per-sensor contacts into stable, honestly-scored tracks.

Association rules follow from what each sensor actually knows:

* **camera -> camera**: both have bearing and range, so gate on Cartesian distance.
* **acoustic -> existing track**: range-only, so gate on range alone. It can *confirm*
  and mark a camera track as moving, but it can never move it, because it has no
  bearing to contribute.
* **network -> existing track**: matched by name only. It supplies a label and nothing
  else, so it never touches geometry.

A track keeps the union of its sources. Corroboration raises confidence; coasting
without any update lowers it until the track is dropped.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .types import Contact, SensorStatus, Snapshot

CAMERA_GATE_M = 0.9      # two camera looks this close are the same person
ACOUSTIC_GATE_M = 0.6    # a range-only echo this close to a track is that track
SMOOTHING = 0.45         # alpha for range/bearing; low enough to reject a single bad frame
COAST_S = 2.5            # keep a track this long after its last update
CONFIRM_S = 0.6          # a camera track must persist this long before we call it a person


@dataclass
class Track:
    id: str
    sources: set[str] = field(default_factory=set)
    distance_m: Optional[float] = None
    bearing_deg: Optional[float] = None
    label: Optional[str] = None
    key: Optional[str] = None      # stable identity for contacts that carry no geometry
    moving: bool = False
    confidence: float = 0.4
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    detail: str = ""

    def as_contact(self) -> Contact:
        return Contact(
            id=self.id,
            source="+".join(sorted(self.sources)) or "unknown",
            distance_m=self.distance_m,
            bearing_deg=self.bearing_deg,
            confidence=self.confidence,
            label=self.label,
            moving=self.moving,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            detail=self.detail,
        )


def _xy(distance: Optional[float], bearing: Optional[float]) -> Optional[tuple[float, float]]:
    if distance is None or bearing is None:
        return None
    rad = math.radians(bearing)
    return distance * math.sin(rad), distance * math.cos(rad)


def _blend(old: Optional[float], new: Optional[float], alpha: float = SMOOTHING) -> Optional[float]:
    if new is None:
        return old
    if old is None:
        return new
    return old + alpha * (new - old)


class Tracker:
    """Stateful fusion across frames. Deterministic: pass `now` to control time in tests."""

    def __init__(self) -> None:
        self._tracks: dict[str, Track] = {}
        self._seq = 0

    def _new_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq}"

    def update(self, contacts: Iterable[Contact], sensors: Optional[list[SensorStatus]] = None,
               now: Optional[float] = None) -> Snapshot:
        now = time.time() if now is None else now
        located, ranged, named = [], [], []
        for c in contacts:
            if c.bearing_deg is not None and c.distance_m is not None:
                located.append(c)
            elif c.distance_m is not None:
                ranged.append(c)
            else:
                named.append(c)

        self._absorb_located(located, now)
        self._absorb_ranged(ranged, now)
        self._absorb_named(named, now)
        self._retire(now)

        tracks = sorted(self._tracks.values(),
                        key=lambda t: (t.distance_m if t.distance_m is not None else 99))
        return Snapshot(contacts=[t.as_contact() for t in tracks],
                        sensors=list(sensors or []), at=now)

    # --- per-kind absorption ---------------------------------------------------
    def _absorb_located(self, contacts: list[Contact], now: float) -> None:
        unmatched = dict(enumerate(contacts))
        for track in self._tracks.values():
            anchor = _xy(track.distance_m, track.bearing_deg)
            if anchor is None:
                continue
            best, best_d = None, CAMERA_GATE_M
            for i, c in unmatched.items():
                point = _xy(c.distance_m, c.bearing_deg)
                d = math.dist(anchor, point)
                if d < best_d:
                    best, best_d = i, d
            if best is not None:
                self._apply(track, unmatched.pop(best), now, geometric=True)
        for c in unmatched.values():
            self._spawn(c, now)

    def _absorb_ranged(self, contacts: list[Contact], now: float) -> None:
        """Range-only evidence confirms a track's presence and motion, never its position."""
        for c in contacts:
            best, best_d = None, ACOUSTIC_GATE_M
            for track in self._tracks.values():
                if track.distance_m is None:
                    continue
                d = abs(track.distance_m - c.distance_m)
                if d < best_d:
                    best, best_d = track, d
            if best is not None:
                self._apply(best, c, now, geometric=False)
            else:
                self._spawn(c, now)

    def _absorb_named(self, contacts: list[Contact], now: float) -> None:
        """Contacts with neither range nor bearing. Matched on a stable identity.

        A passive-audio contact has no label, so matching on label alone spawned a fresh
        track every fusion tick and one talking person became ten contacts. Sensors that
        emit a stable id (the audio sensor always uses "audio-voice") are keyed on that.
        """
        for c in contacts:
            key = c.label or f"{c.source}:{c.id}"
            existing = next((t for t in self._tracks.values() if t.key == key), None)
            if existing is not None:
                self._apply(existing, c, now, geometric=False)
            else:
                self._spawn(c, now, key=key)

    # --- track maintenance -----------------------------------------------------
    def _spawn(self, c: Contact, now: float, key: Optional[str] = None) -> None:
        prefix = {"camera": "p", "acoustic": "e", "network": "n", "audio": "v"}.get(c.source, "t")
        track = Track(id=self._new_id(prefix), sources={c.source}, distance_m=c.distance_m,
                      bearing_deg=c.bearing_deg, label=c.label, key=key, moving=c.moving,
                      confidence=c.confidence, first_seen=now, last_seen=now, detail=c.detail)
        self._tracks[track.id] = track

    def _apply(self, track: Track, c: Contact, now: float, *, geometric: bool) -> None:
        track.sources.add(c.source)
        track.last_seen = now
        track.detail = c.detail
        if geometric:
            track.distance_m = _blend(track.distance_m, c.distance_m)
            track.bearing_deg = _blend(track.bearing_deg, c.bearing_deg)
        elif track.bearing_deg is None:
            # Nothing better exists yet, so a range-only update may still refine the range.
            track.distance_m = _blend(track.distance_m, c.distance_m)
        if c.moving:
            track.moving = True
        if c.label:
            track.label = c.label
        # Corroboration from a second sensor is worth more than a better single reading.
        corroboration = 0.12 * (len(track.sources) - 1)
        track.confidence = round(min(0.99, max(track.confidence, c.confidence) + corroboration), 3)

    def _retire(self, now: float) -> None:
        for tid, track in list(self._tracks.items()):
            idle = now - track.last_seen
            if idle > COAST_S:
                del self._tracks[tid]
            elif idle > 0.5:
                track.confidence = round(track.confidence * 0.85, 3)   # decay while coasting

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks.values())
