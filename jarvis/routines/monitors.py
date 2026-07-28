"""Condition monitors: run a check on a schedule, speak up only when something matters.

A monitor's prompt asks Jarvis to check something; it replies 'ALERT: <one line>' only if the user
should know, else 'NONE'. The scheduler delivers the alert; NONE is silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..agent.autonomous import run_once
from ..config import Config

_SYSTEM = (
    "You are Jarvis running a background monitor for {user}. Use your tools to check the condition. "
    "Reply with a single line 'ALERT: <one spoken sentence>' ONLY if there is something {user} should "
    "know right now; otherwise reply with exactly 'NONE'."
)


@dataclass
class Monitor:
    name: str
    prompt: str
    minutes: int = 30


def parse_alert(text: str) -> Optional[str]:
    stripped = (text or "").strip()
    if stripped.upper().startswith("ALERT:"):
        message = stripped.split(":", 1)[1].strip()
        return message or None
    return None


async def run_monitor(config: Config, monitor: Monitor) -> Optional[str]:
    out = await run_once(
        config, monitor.prompt, system=_SYSTEM.format(user=config.user_name), effort="low"
    )
    return parse_alert(out)


# Default LLM monitors the daemon runs out of the box.
DEFAULT_MONITORS = [
    Monitor(
        "email",
        "Check my unread Gmail with google_email_check ('is:unread'). ALERT with one sentence ONLY if "
        "something is genuinely important or time-sensitive (a real person needing a reply, a deadline, "
        "a bill, travel). Routine/promotional/newsletter mail is NONE.",
        minutes=45,
    ),
]

_battery_warned = [False]


def battery_alert(threshold: int = 20) -> Optional[str]:
    """Pure-Python (no LLM) battery check. Warns once per low episode; resets when charging/recovered."""
    import glob
    import os

    for base in glob.glob("/sys/class/power_supply/BAT*"):
        try:
            with open(os.path.join(base, "capacity")) as f:
                cap = int(f.read().strip())
            with open(os.path.join(base, "status")) as f:
                status = f.read().strip().lower()
        except OSError:
            continue
        if status == "charging" or cap > threshold:
            _battery_warned[0] = False
        elif not _battery_warned[0]:
            _battery_warned[0] = True
            return f"Battery is at {cap}% and not charging — you may want to plug in."
    return None


_resource_warned = {"temp": False, "mem": False}


def resource_alert(temp_c: int = 90, mem_pct: int = 92) -> Optional[str]:
    """Pure-Python check for the machine running hot or low on memory. Warns once per episode."""
    try:
        from ..integrations import system_stats
    except Exception:  # noqa: BLE001
        return None
    d = system_stats.snapshot()
    t = d.get("cpu_temp")
    if t is not None:
        if t >= temp_c and not _resource_warned["temp"]:
            _resource_warned["temp"] = True
            return f"The CPU is running hot at {t:.0f} degrees."
        if t < temp_c - 8:
            _resource_warned["temp"] = False
    mp = d.get("mem", {}).get("percent")
    if mp is not None:
        if mp >= mem_pct and not _resource_warned["mem"]:
            _resource_warned["mem"] = True
            return f"Memory is nearly full at {mp:.0f} percent used."
        if mp < mem_pct - 10:
            _resource_warned["mem"] = False
    return None
