"""Wake-word detection via Picovoice Porcupine ("Jarvis" is a built-in keyword)."""

from __future__ import annotations


class WakeWord:
    def __init__(self, access_key: str, keyword: str = "jarvis", sensitivity: float = 0.5) -> None:
        import pvporcupine  # lazy: only needed in voice mode

        self._p = pvporcupine.create(
            access_key=access_key,
            keywords=[keyword],
            sensitivities=[sensitivity],
        )
        self.frame_length: int = self._p.frame_length
        self.sample_rate: int = self._p.sample_rate

    def process(self, frame) -> bool:
        """Return True if the wake word was detected in this frame."""
        return self._p.process(frame) >= 0

    def delete(self) -> None:
        self._p.delete()
