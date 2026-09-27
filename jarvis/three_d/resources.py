"""Keeping 3D work from starving the live assistant.

The laptop's GPU is small (4 GB) and already carries the voice models. Before Blender starts,
before a preview render and before any heavy provider is considered, these checks say whether
there is room — and if not, why. One render at a time; validation itself runs on the CPU.

(When the release-hardening branch's ``jarvis/resources.py`` lands, these gates should call it
rather than measuring separately.)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

BLENDER_VRAM_MB = 650          # measured: headless Workbench render ≈ 580 MB
BLENDER_RAM_MB = 1200
MIN_DISK_MB = 800
VOICE_RESERVE_MB = 300         # never take the GPU below this much free memory

RENDER_SLOT = threading.BoundedSemaphore(1)
_cache: dict = {}


@dataclass
class Snapshot:
    vram_total_mb: float = 0.0
    vram_free_mb: float = 0.0
    ram_available_mb: float = 0.0
    disk_free_mb: float = 0.0
    gpu: str = ""

    def as_dict(self) -> dict:
        return {k: round(v, 1) if isinstance(v, float) else v for k, v in self.__dict__.items()}


def gpu() -> tuple[str, float, float]:
    """(name, total MB, free MB). Zeros when there is no NVIDIA GPU or no driver tool."""
    hit = _cache.get("gpu")
    if hit and time.time() - hit[0] < 5:
        return hit[1]
    val = ("", 0.0, 0.0)
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
            name, total, free = [x.strip() for x in out.splitlines()[0].split(",")]
            val = (name, float(total), float(free))
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            pass
    _cache["gpu"] = (time.time(), val)
    return val


def ram_available_mb() -> float:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def disk_free_mb(path: str) -> float:
    try:
        p = path
        while p and not os.path.exists(p):
            p = os.path.dirname(p)
        return shutil.disk_usage(p or "/").free / 1e6
    except OSError:
        return 0.0


def snapshot(path: str = os.path.expanduser("~")) -> Snapshot:
    name, total, free = gpu()
    return Snapshot(total, free, ram_available_mb(), disk_free_mb(path), name)


def can_start_blender(project_root: str, snap: Optional[Snapshot] = None) -> tuple[bool, str]:
    s = snap or snapshot(project_root)
    if s.ram_available_mb and s.ram_available_mb < BLENDER_RAM_MB:
        return False, f"only {s.ram_available_mb:.0f} MB of memory is free"
    if s.disk_free_mb and s.disk_free_mb < MIN_DISK_MB:
        return False, f"only {s.disk_free_mb:.0f} MB of disk space is free"
    return True, ""


def can_render(snap: Optional[Snapshot] = None) -> tuple[bool, str]:
    """A GPU preview render only if it leaves the voice service its reserve."""
    s = snap or snapshot()
    if s.vram_total_mb and s.vram_free_mb - BLENDER_VRAM_MB < VOICE_RESERVE_MB:
        return False, (f"the GPU has {s.vram_free_mb:.0f} MB free and the voice service needs its share; "
                       "I'll compare on the CPU instead")
    return True, ""


def free_vram_gb() -> float:
    return gpu()[2] / 1024.0
