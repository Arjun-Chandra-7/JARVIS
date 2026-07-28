"""Local wake-word detection via openWakeWord ("Hey Jarvis"), keyless and offline.

Processes 80 ms (1280-sample) frames at 16 kHz. Exposes the same shape as the cloud WakeWord
(frame_length / sample_rate / process) so the voice session can use either interchangeably.
"""

from __future__ import annotations

import numpy as np


class LocalWakeWord:
    frame_length = 1280  # 80 ms @ 16 kHz — openWakeWord's expected chunk
    sample_rate = 16000

    def __init__(self, threshold: float = 0.5, model: str = "hey_jarvis_v0.1") -> None:
        import os

        import openwakeword
        from openwakeword.model import Model

        # openWakeWord bundles the "hey_jarvis" model + feature models — use the bundled path.
        bundled = os.path.join(
            os.path.dirname(openwakeword.__file__), "resources", "models", f"{model}.onnx"
        )
        wake_ref = bundled if os.path.exists(bundled) else model
        try:
            import openwakeword.utils as oww_utils

            oww_utils.download_models()  # no-op if already present
        except Exception:  # noqa: BLE001
            pass

        self.threshold = threshold
        self._model = Model(wakeword_model_paths=[wake_ref])
        self._armed = True          # refractory gate: one trigger per utterance
        self._hits = 0              # consecutive frames over threshold (needs a sustained match)

    def process(self, frame) -> bool:
        arr = np.asarray(frame, dtype=np.int16)
        scores = self._model.predict(arr)
        best = max(scores.values()) if scores else 0.0

        # Re-arm only after the score clearly falls — prevents a single spike or ongoing
        # conversation from firing repeatedly.
        if best < self.threshold * 0.4:
            self._armed = True

        if best >= self.threshold:
            self._hits += 1
        else:
            self._hits = 0

        # Require two consecutive strong frames AND the gate to be armed.
        if self._hits >= 2 and self._armed:
            self._armed = False
            self._hits = 0
            return True
        return False

    def delete(self) -> None:  # symmetry with the cloud WakeWord
        pass
