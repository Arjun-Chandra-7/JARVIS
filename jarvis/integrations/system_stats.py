"""System health readout: CPU, memory, disk, temperatures, battery, uptime, and NVIDIA GPU.

Uses psutil for the core metrics and nvidia-smi for the GPU (this box has an RTX 3050). Everything
degrades gracefully — a missing sensor just drops that line rather than erroring.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

# Preference order for which sensor counts as "the CPU temperature".
_CPU_TEMP_KEYS = ("k10temp", "coretemp", "cpu_thermal", "acpitz", "zenpower")


def _cpu_temp(temps: dict) -> float | None:
    for key in _CPU_TEMP_KEYS:
        if key in temps and temps[key]:
            return temps[key][0].current
    for entries in temps.values():  # fall back to the first sensor of anything
        if entries:
            return entries[0].current
    return None


def _gpu() -> dict | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=6,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    if not out:
        return None
    parts = [p.strip() for p in out.splitlines()[0].split(",")]
    if len(parts) < 5:
        return None
    try:
        return {
            "util": int(float(parts[0])),
            "mem_used": int(float(parts[1])),
            "mem_total": int(float(parts[2])),
            "temp": int(float(parts[3])),
            "name": parts[4],
        }
    except ValueError:
        return None


def snapshot() -> dict:
    import psutil

    data: dict = {}
    data["cpu_percent"] = psutil.cpu_percent(interval=0.3)
    try:
        data["load"] = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        data["load"] = None

    vm = psutil.virtual_memory()
    data["mem"] = {"used_gb": round(vm.used / 1e9, 1), "total_gb": round(vm.total / 1e9, 1), "percent": vm.percent}
    sw = psutil.swap_memory()
    data["swap_percent"] = sw.percent

    du = psutil.disk_usage("/")
    data["disk"] = {"used_gb": round(du.used / 1e9, 1), "total_gb": round(du.total / 1e9, 1), "percent": du.percent}

    try:
        data["cpu_temp"] = _cpu_temp(psutil.sensors_temperatures())
    except Exception:  # noqa: BLE001
        data["cpu_temp"] = None

    try:
        bat = psutil.sensors_battery()
        if bat is not None:
            data["battery"] = {
                "percent": round(bat.percent),
                "plugged": bat.plugged,
                "mins_left": (bat.secsleft // 60) if bat.secsleft not in (None, -1, -2) else None,
            }
    except Exception:  # noqa: BLE001
        pass

    data["uptime_h"] = round((time.time() - psutil.boot_time()) / 3600, 1)
    data["gpu"] = _gpu()
    return data


def report() -> str:
    """A compact, human/voice-friendly health summary."""
    d = snapshot()
    lines = [f"CPU: {d['cpu_percent']:.0f}%" + (f" ({d['cpu_temp']:.0f}°C)" if d.get("cpu_temp") else "")]
    if d.get("load"):
        lines[0] += f", load {d['load'][0]}"
    m = d["mem"]
    lines.append(f"Memory: {m['used_gb']}/{m['total_gb']} GB used ({m['percent']:.0f}%)")
    g = d.get("gpu")
    if g:
        lines.append(
            f"GPU ({g['name']}): {g['util']}% util, {g['mem_used']}/{g['mem_total']} MB, {g['temp']}°C"
        )
    dk = d["disk"]
    lines.append(f"Disk /: {dk['used_gb']}/{dk['total_gb']} GB ({dk['percent']:.0f}%)")
    b = d.get("battery")
    if b:
        state = "charging" if b["plugged"] else "on battery"
        extra = f", ~{b['mins_left']}m left" if b.get("mins_left") else ""
        lines.append(f"Battery: {b['percent']}% ({state}{extra})")
    lines.append(f"Uptime: {d['uptime_h']}h")
    return "\n".join(lines)
