"""Local text-to-speech via Piper (keyless, offline), streamed sentence by sentence.

Two things used to make Jarvis feel slow to answer, both fixed here:

* **A new `piper` process per reply.** Loading the voice model costs ~1.8 s, and that was paid on
  every single thing Jarvis said. The voice is now loaded once and kept in memory, so a sentence
  synthesises in 170-320 ms (RTF ~0.06) instead of 1.4-1.7 s.
* **Synthesising the whole reply before playing any of it.** A long answer began with seconds of
  silence. Playback now starts on the first sentence while the rest is still being synthesised.

Falls back to the `piper` CLI if the Python API is unavailable, so this keeps working on installs
where only the executable is present.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterator, Optional

_voice = None
_voice_path: Optional[str] = None
_voice_lock = threading.Lock()

# Split on sentence enders, keeping the punctuation. Long clauses are split further on commas so
# the first audio still arrives quickly when a "sentence" runs on.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_END = re.compile(r"(?<=[,;:])\s+")
MAX_CHUNK_CHARS = 180


def _piper_cmd() -> list[str]:
    exe = Path(sys.executable).with_name("piper")
    return [str(exe)] if exe.exists() else [sys.executable, "-m", "piper"]


def _sample_rate(model_path: str) -> int:
    cfg = Path(str(model_path) + ".json")
    try:
        return int(json.loads(cfg.read_text())["audio"]["sample_rate"])
    except Exception:  # noqa: BLE001
        return 22050


def split_for_speech(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Break a reply into speakable chunks, preferring sentence then clause boundaries.

    Pure function so the chunking rules can be unit-tested without audio hardware.
    """
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        # Too long to wait for: split on clauses, then hard-wrap whatever is still oversized.
        buf = ""
        for piece in _CLAUSE_END.split(sentence):
            if buf and len(buf) + 1 + len(piece) > max_chars:
                chunks.append(buf.strip())
                buf = piece
            else:
                buf = f"{buf} {piece}".strip()
        while len(buf) > max_chars:
            cut = buf.rfind(" ", 0, max_chars) or max_chars
            chunks.append(buf[:cut].strip())
            buf = buf[cut:].strip()
        if buf:
            chunks.append(buf)
    return [c for c in chunks if c]


def _get_voice(model_path: str):
    """Load and cache the Piper voice. Returns None when the Python API isn't usable."""
    global _voice, _voice_path
    with _voice_lock:
        if _voice is not None and _voice_path == str(model_path):
            return _voice
        try:
            from piper import PiperVoice

            _voice = PiperVoice.load(model_path)
            _voice_path = str(model_path)
        except Exception:  # noqa: BLE001 - fall back to the CLI
            _voice = None
            _voice_path = None
        return _voice


def warmup(model_path: str) -> bool:
    """Load the voice ahead of first use so the first reply isn't slow. True if the API is live."""
    return _get_voice(model_path) is not None


def _synth_chunk_api(voice, text: str) -> tuple[bytes, int]:
    pcm = bytearray()
    rate = 22050
    for chunk in voice.synthesize(text):
        pcm.extend(chunk.audio_int16_bytes)
        rate = chunk.sample_rate
    return bytes(pcm), rate


def _synth_chunk_cli(text: str, model_path: str) -> tuple[bytes, int]:
    proc = subprocess.run(
        _piper_cmd() + ["-m", str(model_path), "--output-raw"],
        input=text.encode("utf-8"),
        capture_output=True,
    )
    return proc.stdout, _sample_rate(model_path)


def synth(text: str, model_path: str) -> tuple[bytes, int]:
    """Synthesise the whole text at once. Kept for callers that want a single buffer."""
    text = (text or "").strip()
    if not text:
        return b"", _sample_rate(model_path)
    voice = _get_voice(model_path)
    if voice is not None:
        return _synth_chunk_api(voice, text)
    return _synth_chunk_cli(text, model_path)


def synth_stream(
    text: str, model_path: str, stop_event: Optional[threading.Event] = None
) -> Iterator[tuple[bytes, int]]:
    """Yield (pcm, sample_rate) per speakable chunk, synthesising as the caller consumes."""
    voice = _get_voice(model_path)
    for chunk in split_for_speech(text):
        if stop_event is not None and stop_event.is_set():
            return
        if voice is not None:
            yield _synth_chunk_api(voice, chunk)
        else:
            yield _synth_chunk_cli(chunk, model_path)


def _peak(pcm: bytes) -> float:
    """Loudest sample in a block, 0..1 — what the HUD's speaking animation follows."""
    if not pcm:
        return 0.0
    import array

    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not a:
        return 0.0
    return min(1.0, max(abs(min(a)), abs(max(a))) / 32768.0)


def speak(
    text: str,
    model_path: str,
    output_device: int = -1,
    stop_event: Optional[threading.Event] = None,
    on_first_audio: Optional[callable] = None,
    on_level: Optional[callable] = None,
) -> None:
    """Speak `text`, starting playback on the first sentence.

    A worker synthesises ahead while the main thread plays, so the gap between sentences is
    covered by the audio already queued. `stop_event` cuts playback promptly — it is checked
    between 4 KB writes, not just between sentences.
    """
    text = (text or "").strip()
    if not text:
        return

    import sounddevice as sd

    pending: "queue.Queue[Optional[tuple[bytes, int]]]" = queue.Queue(maxsize=2)

    def produce() -> None:
        try:
            for item in synth_stream(text, model_path, stop_event):
                if stop_event is not None and stop_event.is_set():
                    break
                pending.put(item)
        except Exception:  # noqa: BLE001 - never let a synth failure hang the player
            pass
        finally:
            pending.put(None)

    worker = threading.Thread(target=produce, daemon=True)
    worker.start()

    device = None if output_device < 0 else output_device
    stream = None
    announced = False
    try:
        while True:
            item = pending.get()
            if item is None:
                break
            pcm, rate = item
            if not pcm:
                continue
            if stop_event is not None and stop_event.is_set():
                break
            if stream is None:
                stream = sd.RawOutputStream(
                    samplerate=rate, channels=1, dtype="int16", device=device
                )
                stream.start()
            if not announced:
                announced = True
                if on_first_audio is not None:
                    try:
                        on_first_audio()
                    except Exception:  # noqa: BLE001
                        pass
            chunk = 4096
            for i in range(0, len(pcm), chunk):
                if stop_event is not None and stop_event.is_set():
                    break
                block = pcm[i : i + chunk]
                if on_level is not None:
                    on_level(_peak(block))
                stream.write(block)
    finally:
        if stream is not None:
            stream.stop()
            stream.close()
        # Drain so the producer thread can finish instead of blocking on a full queue.
        try:
            while pending.get_nowait() is not None:
                pass
        except queue.Empty:
            pass
