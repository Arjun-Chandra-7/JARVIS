import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from jarvis.audio.vad import record_utterance
from jarvis.commands import handle
from jarvis.config import Config
from jarvis.preferences import notifications_enabled


class VoiceTests(unittest.TestCase):
    def test_notification_preference_survives_reads(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"JARVIS_STATE_DIR": folder}):
            result = asyncio.run(handle("Jarvis turn off notifications.", Config()))
            self.assertIn("off", result)
            self.assertFalse(notifications_enabled())
            asyncio.run(handle("Jarvis turn on notifications", Config()))
            self.assertTrue(notifications_enabled())

    def test_capture_keeps_quiet_onset(self):
        frames = iter([np.full(160, 20)] * 3 + [np.full(160, 500)] * 25 + [np.zeros(160)] * 6)
        pcm = record_utterance(lambda: next(frames, None), sample_rate=16000,
                               frame_length=160, threshold=100, silence_ms=50, max_s=3, wait_s=1)
        self.assertEqual(np.frombuffer(pcm, dtype=np.int16)[0], 20)

    def test_local_resample_and_vocabulary(self):
        from jarvis.audio import local_stt
        from types import SimpleNamespace
        class Model:
            def transcribe(self, audio, **kw):
                self.audio, self.kw = audio, kw
                return iter([SimpleNamespace(text="open my phone", no_speech_prob=.1, avg_logprob=-.2)]), None
        model = Model()
        with patch.object(local_stt, "_get_model", return_value=model):
            result = local_stt.transcribe(np.zeros(48000, dtype=np.int16).tobytes(), 48000)
        self.assertEqual(len(model.audio), 16000)
        self.assertEqual(result, "open my phone")
        self.assertIn("Jarvis", model.kw["initial_prompt"])


if __name__ == "__main__":
    unittest.main()
