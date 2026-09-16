"""Microphone capture via sounddevice (16 kHz, mono, int16 frames).

sounddevice talks to PortAudio's PipeWire/Pulse backends, so device -1 uses the system default
input (your real mic) — unlike pvrecorder, which often only enumerates raw ALSA/monitor devices.

Captured at the device's own channel count and mixed down here, never by asking the device for
one channel. That is not a preference; asking "default" for mono silently returns garbage.

Measured on this machine, the same device in the same second:

    default, 1 channel    peak 0.5989  rms 0.48880  a 750 Hz square wave
    default, 2 channels   peak 0.0001  rms 0.00006  the actual, quiet room

The "tone" is not a signal. "default" advertises 128 input channels at 44.1 kHz — it is the
audio server's catch-all, not a sound card — and a mono 16 kHz stream pulled out of it comes
back as a misread of the real one. It is perfectly steady, loud, and arrives forever, so the
endpointer sees a device that is shouting something that is not speech and captures nothing.
The log said `no audio captured, loudest frame 0.5975` four times, with the same number each
time, which is what gave it away: a room does not repeat itself to four decimal places.
"""

from __future__ import annotations

from typing import List


class Microphone:
    def __init__(self, frame_length: int, device_index: int = -1) -> None:
        import sounddevice as sd  # lazy: only needed in voice mode

        self.frame_length = frame_length
        device = None if device_index < 0 else device_index
        self.channels = self._native_channels(sd, device)
        self._stream = sd.InputStream(
            samplerate=16000,
            channels=self.channels,
            dtype="int16",
            blocksize=frame_length,
            device=device,
        )

    @staticmethod
    def _native_channels(sd, device) -> int:
        """How many channels to ask the device for.

        Two when it has them, because that is what a real capture device offers and what the
        server's catch-all device can actually deliver; one only when the device genuinely is
        mono, such as a headset's own microphone addressed directly. Never more than two — the
        catch-all claims 128, which is a maximum rather than an offer.
        """
        try:
            most = int(sd.query_devices(device, kind="input")["max_input_channels"])
        except Exception:  # noqa: BLE001 — an unqueryable device still deserves an attempt
            return 2
        return 2 if most >= 2 else 1

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        if self._stream.active:
            self._stream.stop()

    def read(self) -> List[int]:
        """Block for one frame of `frame_length` int16 samples (a plain list of ints)."""
        data, _overflowed = self._stream.read(self.frame_length)
        # The first channel, never a mix of them. Measured on the same stream, same second:
        #
        #     channel 0   peak 0.00052   rms 0.000093    the real, quiet room
        #     channel 1   peak 1.00284   rms 0.939308    750 Hz, full scale, forever
        #
        # The catch-all device's second channel carries nothing real, so averaging the two — which
        # looks like the careful thing to do, and was tried — puts a full-scale tone at half
        # amplitude over the microphone and hides the speech underneath it.
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
