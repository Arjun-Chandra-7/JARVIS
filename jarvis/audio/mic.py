"""Microphone capture via sounddevice (16 kHz, mono, int16 frames).

sounddevice talks to PortAudio's PipeWire/Pulse backends, so device -1 uses the system default
input (your real mic) — unlike pvrecorder, which often only enumerates raw ALSA/monitor devices.
"""

from __future__ import annotations

from typing import List


class Microphone:
    def __init__(self, frame_length: int, device_index: int = -1) -> None:
        import sounddevice as sd  # lazy: only needed in voice mode

        self.frame_length = frame_length
        device = None if device_index < 0 else device_index
        self._stream = sd.InputStream(
            samplerate=16000,
            channels=1,
            dtype="int16",
            blocksize=frame_length,
            device=device,
        )

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        if self._stream.active:
            self._stream.stop()

    def read(self) -> List[int]:
        """Block for one frame of `frame_length` int16 samples (a plain list of ints)."""
        data, _overflowed = self._stream.read(self.frame_length)
        return data[:, 0].tolist()

    def delete(self) -> None:
        self._stream.close()

    @staticmethod
    def list_devices() -> list[str]:
        import sounddevice as sd

        return [
            f"{i}: {d['name']}"
            for i, d in enumerate(sd.query_devices())
            if d.get("max_input_channels", 0) > 0
        ]
