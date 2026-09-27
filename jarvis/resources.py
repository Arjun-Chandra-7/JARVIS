"""How much room the shared machine has, asked cheaply, so heavy things go where they fit.

The 4 GB card is shared by Ollama, speech recognition and whatever else is running; a model
loaded onto a nearly-full card fails later, mid-utterance, with a cuBLAS allocation error. So a
GPU model is only loaded when there is measurably room for it, and otherwise goes to the
processor at once. Answers are cached briefly: this is asked on the voice path.

Nothing here kills or pauses another process.
"""
from __future__ import annotations

import subprocess
import threading
import time
from typing import Optional

_CACHE: dict[str, tuple[float, object]] = {}
_LOCK = threading.Lock()
TTL_S = 15.0

# Measured on this machine (int8): whisper small ~480 MiB resident on the card, base ~250 MiB,
# plus working memory for a 10-second utterance.
MODEL_MIB = {"tiny": 200, "base": 350, "small": 700, "medium": 1600}


def gpu_memory() -> Optional[tuple[int, int]]:
    """(used, total) MiB of the first GPU, or None when there is no working driver."""
    with _LOCK:
        hit = _CACHE.get("gpu")
        if hit and time.monotonic() - hit[0] < TTL_S:
            return hit[1]  # type: ignore[return-value]
    value: Optional[tuple[int, int]] = None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3)
        used, total = (int(x) for x in out.stdout.strip().splitlines()[0].split(",")[:2])
        value = (used, total)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        value = None
    with _LOCK:
        _CACHE["gpu"] = (time.monotonic(), value)
    return value


def room_on_gpu(model_name: str, margin_mib: int = 150) -> bool:
    """Whether `model_name` fits on the card right now. Unknown sizes are treated as small's."""
    mem = gpu_memory()
    if mem is None:
        return False
    used, total = mem
    need = MODEL_MIB.get(model_name.split(".")[0], MODEL_MIB["small"]) + margin_mib
    return total - used >= need


def forget() -> None:
    with _LOCK:
        _CACHE.clear()
