"""Text-to-speech via ElevenLabs streaming (PCM out), played through sounddevice.

Plain HTTP (httpx) streaming to avoid SDK churn and ffmpeg; PCM is written straight to a
sounddevice output stream so playback starts as audio arrives and can be interrupted (barge-in)
by setting `stop_event`.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import httpx


def speak(
    text: str,
    api_key: str,
    voice_id: str,
    model_id: str = "eleven_turbo_v2_5",
    sample_rate: int = 16000,
    output_device: int = -1,
    stop_event: Optional[threading.Event] = None,
    on_started: Optional[Callable[[], None]] = None,
    timeout: float = 60.0,
) -> None:
    text = (text or "").strip()
    if not text:
        return

    import sounddevice as sd  # lazy: only needed in voice mode

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    params = {"output_format": f"pcm_{sample_rate}"}
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    body = {"text": text, "model_id": model_id}
    device = None if output_device < 0 else output_device

    stream = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16", device=device)
    stream.start()
    try:
        with httpx.stream("POST", url, params=params, headers=headers, json=body, timeout=timeout) as r:
            r.raise_for_status()
            if on_started is not None:
                on_started()
            for chunk in r.iter_bytes():
                if stop_event is not None and stop_event.is_set():
                    break
                if chunk:
                    stream.write(chunk)
    finally:
        stream.stop()
        stream.close()
