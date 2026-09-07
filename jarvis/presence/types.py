"""Shared presence data model.

The important invariant: ``bearing_deg is None`` means the bearing is genuinely
unknown, not zero. Consumers must render such a contact as a range arc rather than
a point, and ``position()`` returns ``None`` for it.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Where a contact came from, weakest evidence last.
SOURCES = ("camera", "acoustic", "audio", "network")


@dataclass
class Contact:
    """One sensed person (or, for acoustic/network, one sensed *presence*)."""

    id: str
    source: str
    distance_m: Optional[float] = None      # metres, None when unknown
    bearing_deg: Optional[float] = None     # +right / -left of the screen normal; None = unknown
    confidence: float = 0.5                 # 0..1, how much to trust this contact
    label: Optional[str] = None             # a name, when identity is established
    moving: bool = False
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    detail: str = ""

    def position(self) -> Optional[tuple[float, float]]:
        """Cartesian (x right, y forward) in metres — only when both range and bearing exist."""
        if self.distance_m is None or self.bearing_deg is None:
            return None
        rad = math.radians(self.bearing_deg)
        return (self.distance_m * math.sin(rad), self.distance_m * math.cos(rad))

    def age(self, now: Optional[float] = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.last_seen)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        pos = self.position()
        data["x_m"] = round(pos[0], 3) if pos else None
        data["y_m"] = round(pos[1], 3) if pos else None
        data["bearing_known"] = self.bearing_deg is not None
        if self.distance_m is not None:
            data["distance_m"] = round(self.distance_m, 2)
        if self.bearing_deg is not None:
            data["bearing_deg"] = round(self.bearing_deg, 1)
        data["confidence"] = round(self.confidence, 2)
        return data


@dataclass
class SensorStatus:
    """Whether one sensor is contributing, and why not when it isn't."""

    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class Snapshot:
    contacts: list[Contact] = field(default_factory=list)
    sensors: list[SensorStatus] = field(default_factory=list)
    at: float = field(default_factory=time.time)

    @property
    def people(self) -> int:
        """How many distinct people we can actually stand behind (bearing or identity known)."""
        return sum(1 for c in self.contacts
                   if c.confidence >= 0.5 and (c.bearing_deg is not None or c.label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "age": round(max(0.0, time.time() - self.at), 2),
            "people": self.people,
            "count": len(self.contacts),
            "contacts": [c.to_dict() for c in self.contacts],
            "sensors": [s.to_dict() for s in self.sensors],
        }
