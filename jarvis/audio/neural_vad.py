"""Silero VAD — decides whether a frame contains *speech*, not merely *sound*.

The energy detector in `vad.py` compares RMS against a threshold calibrated from ambient noise.
That is a loudness test, and loudness is a poor proxy for speech on a laptop: a keyboard, a desk
knock, a fan spinning up and the assistant's own speaker all clear the bar, while a quietly spoken
sentence from across the room does not. Every false positive costs a Whisper transcription and,
during playback, a spurious barge-in.

Silero is a small ONNX classifier that answers the right question. Checking three synthetic inputs
against it makes the difference concrete — digital silence, white noise and a pure 800 Hz tone all
score below 0.002, and energy VAD would have fired on two of them.

It is optional in every direction. The model is fetched once on first use and cached; if the
download fails, onnxruntime is missing, or `JARVIS_NEURAL_VAD=0` is set, `load()` returns None and
callers keep the energy path. Inference is ~1 ms per 32 ms window on a CPU core, so it costs
nothing next to the models already resident on the GPU.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

MODEL_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"

# Silero v5 is trained on fixed windows: 512 samples at 16 kHz, 256 at 8 kHz. Anything else and
# the scores are meaningless, so audio is buffered to exactly this size.
WINDOW_16K = 512
WINDOW_8K = 256

_LOAD_LOCK = threading.Lock()
_CACHED: dict[str, Optional["SileroVAD"]] = {}


def model_path() -> Path:
    root = Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()
    return root / "silero_vad.onnx"


def enabled() -> bool:
    return os.environ.get("JARVIS_NEURAL_VAD", "1").strip().lower() not in {"0", "false", "no", "off"}


def ensure_model(timeout: float = 30.0) -> Optional[Path]:
    """Return the local model path, downloading it once if needed. None if unavailable offline."""
    path = model_path()
    if path.exists() and path.stat().st_size > 100_000:
        return path
    try:
        import httpx

        path.parent.mkdir(parents=True, exist_ok=True)
        data = httpx.get(MODEL_URL, timeout=timeout, follow_redirects=True).content
        if len(data) < 100_000:            # a captive portal or error page, not a model
            return None
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)                  # atomic: never leave a half-written model behind
        return path
    except Exception:  # noqa: BLE001 - offline is a normal state, not an error
        return None


class SileroVAD:
    """Streaming speech detector. Feed it int16 frames of any length; it buffers to Silero's window.

    The recurrent state is carried between windows, so this must be `reset()` at the start of each
    utterance — a stale state from the previous phrase biases the first few frames of the next.
    """

    def __init__(self, session, sample_rate: int = 16000) -> None:
        self.session = session
        self.sample_rate = sample_rate
        self.window = WINDOW_16K if sample_rate >= 16000 else WINDOW_8K
        self._sr = np.array(sample_rate, dtype=np.int64)
        self._tail = np.zeros(0, dtype=np.float32)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)
        self.last_prob = 0.0

    def feed(self, frame) -> float:
        """Push one audio frame; return the highest speech probability across complete windows.

        Returns the previous score when the frame is shorter than a window, so a caller polling
        every 20 ms still gets a sensible answer rather than a zero.
        """
        arr = np.asarray(frame, dtype=np.int16).ravel().astype(np.float32) / 32768.0
        buf = np.concatenate((self._tail, arr)) if self._tail.size else arr

        best = None
        n = buf.size // self.window
        for i in range(n):
            chunk = buf[i * self.window : (i + 1) * self.window][None, :]
            try:
                out, self._state = self.session.run(
                    None, {"input": chunk, "state": self._state, "sr": self._sr}
                )
            except Exception:  # noqa: BLE001 - a bad frame must not kill the capture loop
                return self.last_prob
            p = float(out[0][0])
            best = p if best is None else max(best, p)

        self._tail = buf[n * self.window :] if n else buf
        if best is not None:
            self.last_prob = best
        return self.last_prob


def load(sample_rate: int = 16000) -> Optional[SileroVAD]:
    """Cached loader. Returns None whenever the neural path isn't available — never raises."""
    if not enabled():
        return None
    key = str(sample_rate)
    if key in _CACHED:
        return _CACHED[key]
    with _LOAD_LOCK:
        if key in _CACHED:
            return _CACHED[key]
        vad: Optional[SileroVAD] = None
        try:
            import onnxruntime as ort

            path = ensure_model()
            if path is not None:
                opts = ort.SessionOptions()
                # One thread: this runs alongside Whisper and Piper, and a 512-sample window does
                # not benefit from parallelism anyway.
                opts.inter_op_num_threads = 1
                opts.intra_op_num_threads = 1
                opts.log_severity_level = 3
                session = ort.InferenceSession(
                    str(path), sess_options=opts, providers=["CPUExecutionProvider"]
                )
                vad = SileroVAD(session, sample_rate)
        except Exception:  # noqa: BLE001
            vad = None
        _CACHED[key] = vad
        return vad


def describe() -> str:
    """One line for `--check`, saying what the voice loop will actually use."""
    if not enabled():
        return "neural VAD disabled (JARVIS_NEURAL_VAD=0) — using energy threshold"
    if model_path().exists():
        return f"Silero VAD ready ({model_path()})"
    return "Silero VAD not downloaded yet — fetched on first use, energy threshold until then"
