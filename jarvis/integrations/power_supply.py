"""Mains/battery state and plug-in transitions, read straight from sysfs.

sysfs is preferred over psutil here because it distinguishes "Full" from "Charging",
which is the difference between "charged" and "charging" in what Jarvis says.
"""
from __future__ import annotations

import glob
from pathlib import Path
from typing import Any, Optional

_BATTERY_GLOB = "/sys/class/power_supply/BAT*"
_MAINS_GLOB = "/sys/class/power_supply/*"


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read() -> dict[str, Any]:
    """Current power state: {present, plugged, percent, status}."""
    percent: Optional[int] = None
    status = ""
    present = False
    for base in sorted(glob.glob(_BATTERY_GLOB)):
        raw = _read(Path(base) / "capacity")
        if raw is None:
            continue
        present = True
        try:
            percent = int(raw)
        except ValueError:
            percent = None
        status = (_read(Path(base) / "status") or "").strip()
        break

    plugged: Optional[bool] = None
    for base in sorted(glob.glob(_MAINS_GLOB)):
        if (_read(Path(base) / "type") or "") != "Mains":
            continue
        online = _read(Path(base) / "online")
        if online is not None:
            plugged = online == "1"
            break
    if plugged is None:                       # no mains node: infer from the battery's own status
        plugged = status.lower() in {"charging", "full"}

    if not present:                           # desktop / no battery reported
        try:
            import psutil
            bat = psutil.sensors_battery()
            if bat is not None:
                present, percent, plugged = True, int(bat.percent), bool(bat.power_plugged)
                status = "Charging" if plugged else "Discharging"
        except Exception:  # noqa: BLE001
            pass

    return {"present": present, "plugged": bool(plugged), "percent": percent, "status": status}


def _pct(state: dict[str, Any]) -> str:
    pct = state.get("percent")
    return f"{pct} percent" if isinstance(pct, int) else "unknown charge"


def spoken_state(state: Optional[dict[str, Any]] = None) -> str:
    """One clause for a briefing: 'charging, battery 76 percent'."""
    state = read() if state is None else state
    if not state.get("present"):
        return "no battery detected"
    if state.get("plugged"):
        charged = (state.get("status") or "").lower() == "full" or state.get("percent") == 100
        return f"{'charged' if charged else 'charging'}, battery {_pct(state)}"
    return f"on battery, {_pct(state)}"


class PowerWatcher:
    """Emits a sentence only when the mains state actually changes."""

    def __init__(self) -> None:
        self._plugged: Optional[bool] = None

    def poll(self, state: Optional[dict[str, Any]] = None) -> Optional[str]:
        """Return an announcement on a plug/unplug transition, else None."""
        state = read() if state is None else state
        if not state.get("present"):
            return None
        plugged = bool(state.get("plugged"))
        first = self._plugged is None
        changed = not first and plugged != self._plugged
        self._plugged = plugged
        if not changed:
            return None                       # first observation is a baseline, not an event
        if plugged:
            charged = (state.get("status") or "").lower() == "full" or state.get("percent") == 100
            if charged:
                return f"Laptop charging, battery {_pct(state)} — already full, sir."
            return f"Laptop charging, battery {_pct(state)}."
        return f"Charger unplugged, sir. Battery {_pct(state)}."
