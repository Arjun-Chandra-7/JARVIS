"""Local text-to-speech via Piper (keyless, offline). Synthesizes raw PCM, plays via sounddevice.

Uses the `piper` CLI (stable across versions): text in on stdin, raw 16-bit PCM out on stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional


def _piper_cmd() -> list[str]:
    exe = Path(sys.executable).with_name("piper")
    return [str(exe)] if exe.exists() else [sys.executable, "-m", "piper"]


def _sample_rate(model_path: str) -> int:
    cfg = Path(str(model_path) + ".json")
    try:
        return int(json.loads(cfg.read_text())["audio"]["sample_rate"])
    except Exception:  # noqa: BLE001
        return 22050


def synth(text: str, model_path: str) -> tuple[bytes, int]:
    text = (text or "").strip()
    sr = _sample_rate(model_path)
    if not text:
        return b"", sr
    proc = subprocess.run(
        _piper_cmd() + ["-m", str(model_path), "--output-raw"],
        input=text.encode("utf-8"),
        capture_output=True,
    )
    return proc.stdout, sr


def speak(
    text: str,
    model_path: str,
    output_device: int = -1,
    stop_event: Optional[threading.Event] = None,
) -> None:
    pcm, sr = synth(text, model_path)
    if not pcm:
        return
    import sounddevice as sd

    device = None if output_device < 0 else output_device
    stream = sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16", device=device)
    stream.start()
    try:
        chunk = 4096
        for i in range(0, len(pcm), chunk):
            if stop_event is not None and stop_event.is_set():
                break
            stream.write(pcm[i : i + chunk])
    finally:
        stream.stop()
        stream.close()
