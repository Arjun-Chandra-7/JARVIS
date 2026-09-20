"""A voice that does not sound like a phone menu.

Piper is fast, tiny and offline, and it is also unmistakably a speech synthesiser. Kokoro is an
82M-parameter StyleTTS2-lineage model — about 330 MB of weights, Apache-2.0, faster than realtime
on this processor — and the difference is audible in the first sentence.

It also fits the one hard constraint here. The card has 4 GB and roughly 674 MiB of it free with
Ollama and Whisper resident, which rules out nearly every 2026 TTS model worth having: Chatterbox
wants about ten gigabytes, Kyutai's work is benchmarked on datacentre cards. Kokoro runs on the
processor at a few times realtime and would fit in the spare VRAM if it ever needed to.

About "emotion"
---------------
Kokoro has no emotion conditioning. Anything claiming otherwise about this model is wrong, and
building a feature on top of a capability the weights do not have is how you get a setting that
appears to do something and does not.

What it does have is fifty-four voices and a speed control, and those two are enough for
*delivery* — the difference between reading a confirmation and reading a failure. That is what
`Delivery` below is: a small, honest amount of variation, chosen per utterance, with the reason
written next to each one. Real, and modest, and not called emotion.

Weights are not fetched automatically
-------------------------------------
The same rule the picture model follows. 330 MB is not something to download because somebody
said good morning, and a disk with 24 GB free is not a place to be casual. Until the package and
the weights are both present, `available()` is False and the voice stays Piper — so Jarvis can
never promise a voice it has no way to produce.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import Iterator, Optional

# Kokoro speaks at 24 kHz. Piper's models here are 16 or 22.05 kHz, so anything downstream has to
# take the rate from the synthesiser rather than assume one — which is why `synth` returns it.
SAMPLE_RATE = 24000

# British male. JARVIS is a particular voice and this is the closest of the fifty-four.
DEFAULT_VOICE = os.environ.get("JARVIS_KOKORO_VOICE", "bm_george")

# American English 'a', British English 'b'. Must agree with the voice's first letter or the
# grapheme-to-phoneme stage mispronounces its way through everything.
_LANG_FOR_PREFIX = {"a": "a", "b": "b"}

_pipeline = None
_pipeline_voice: Optional[str] = None
_lock = threading.Lock()


@dataclass(frozen=True)
class Delivery:
    """How a line should be read. Pace and voice — not emotion, which this model has none of."""

    name: str
    speed: float
    voice: Optional[str] = None
    why: str = ""


# Deliberately narrow. Four of these are memorable; twenty would be a settings screen nobody
# tunes and a model asked to pick between shades it cannot produce.
NEUTRAL = Delivery("neutral", 1.0, why="the default: answers, readings, most of everything")
BRISK = Delivery("brisk", 1.09, why="acknowledgements — 'on it, sir' should not be savoured")
GRAVE = Delivery("grave", 0.92, why="failures and warnings, where slowing down is the signal")
WARM = Delivery("warm", 0.97, why="greetings and goodbyes, the two lines that are not work")

DELIVERIES = {d.name: d for d in (NEUTRAL, BRISK, GRAVE, WARM)}

# Chosen from the words, before any model sees them — the same deterministic-layer-first habit
# the rest of the program follows. A model asked to tag its own tone on every utterance would
# cost a round trip and get it wrong in a new way each time.
_GREETING = re.compile(r"(?i)^\s*(good\s+(morning|afternoon|evening|night)|hello|hi\b|welcome\s+back"
                       r"|goodnight|good\s*bye|see\s+you|sleep\s+well)")
_TROUBLE = re.compile(r"(?i)\b(failed|failure|error|could\s+not|couldn'?t|unable|denied|refused"
                      r"|went\s+wrong|no\s+response|timed?\s+out|offline|not\s+found)\b")
_ACK = re.compile(r"(?i)^\s*(on\s+it|right\s+away|of\s+course|certainly|done|got\s+it|understood"
                  r"|yes,?\s+sir|very\s+good|as\s+you\s+wish)\b")


def delivery_for(text: str) -> Delivery:
    """Which of the four a line should be read with."""
    said = (text or "").strip()
    if not said:
        return NEUTRAL
    # Trouble first: "Good morning. The backup failed." is a failure, whatever it opens with.
    if _TROUBLE.search(said):
        return GRAVE
    if _GREETING.match(said):
        return WARM
    if _ACK.match(said):
        return BRISK
    return NEUTRAL


def _weights_present() -> bool:
    """Whether the model is on disk already. Never downloads to find out."""
    from pathlib import Path

    home = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    hub = Path(home) / "hub"
    if not hub.exists():
        return False
    return any(hub.glob("models--hexgrad--Kokoro-82M*/snapshots/*/*.pth")) or \
        any(hub.glob("models--hexgrad--Kokoro-82M*/snapshots/*/*.safetensors"))


def available() -> bool:
    """Both halves present: the package and the weights."""
    try:
        import kokoro  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return _weights_present()


def how_to_install() -> str:
    """What to do about it, said once, where the user can act on it."""
    try:
        import kokoro  # noqa: F401
    except Exception:  # noqa: BLE001
        return ("Kokoro is not installed. For a better voice:\n"
                "    .venv/bin/pip install kokoro soundfile\n"
                "    sudo apt install espeak-ng")
    if not _weights_present():
        return ("Kokoro is installed but its weights are not on disk (about 330 MB). "
                "Fetch them into the cache this machine already uses:\n"
                "    HF_HOME=~/Madara/.cache/huggingface .venv/bin/python -c "
                "\"from kokoro import KPipeline; KPipeline(lang_code='b')\"")
    return ""


def _get_pipeline(voice: str):
    """One pipeline per voice family, built on first use and kept."""
    global _pipeline, _pipeline_voice
    lang = _LANG_FOR_PREFIX.get(voice[:1], "a")
    with _lock:
        if _pipeline is not None and _pipeline_voice == lang:
            return _pipeline
        from kokoro import KPipeline

        _pipeline = KPipeline(lang_code=lang)
        _pipeline_voice = lang
        return _pipeline


def _to_pcm16(samples) -> bytes:
    """float32 -1..1 from the model, int16 for the same output path Piper's audio takes."""
    import numpy as np

    array = np.asarray(samples, dtype=np.float32)
    if array.size == 0:
        return b""
    # Clip rather than normalise: normalising makes a quiet line as loud as a shout, which
    # removes exactly the variation this module exists to keep.
    array = np.clip(array, -1.0, 1.0)
    return (array * 32767.0).astype(np.int16).tobytes()


def synth(text: str, voice: str = "", delivery: Optional[Delivery] = None) -> tuple[bytes, int]:
    """The whole line as one buffer, matching local_tts.synth's shape."""
    said = (text or "").strip()
    if not said:
        return b"", SAMPLE_RATE
    chosen = delivery or delivery_for(said)
    use_voice = voice or chosen.voice or DEFAULT_VOICE
    pipeline = _get_pipeline(use_voice)
    chunks: list[bytes] = []
    for result in pipeline(said, voice=use_voice, speed=chosen.speed):
        audio = getattr(result, "audio", None)
        if audio is None and isinstance(result, tuple):
            audio = result[-1]
        if audio is not None:
            chunks.append(_to_pcm16(audio))
    return b"".join(chunks), SAMPLE_RATE


def synth_stream(text: str, voice: str = "", delivery: Optional[Delivery] = None,
                 stop_event: Optional[threading.Event] = None) -> Iterator[tuple[bytes, int]]:
    """Yield (pcm, rate) per segment, so speech starts before the line is finished.

    Kokoro's pipeline already splits on sentence boundaries, so this hands each one on as it
    arrives rather than imposing a second splitter on top of it.
    """
    said = (text or "").strip()
    if not said:
        return
    chosen = delivery or delivery_for(said)
    use_voice = voice or chosen.voice or DEFAULT_VOICE
    pipeline = _get_pipeline(use_voice)
    for result in pipeline(said, voice=use_voice, speed=chosen.speed):
        if stop_event is not None and stop_event.is_set():
            return
        audio = getattr(result, "audio", None)
        if audio is None and isinstance(result, tuple):
            audio = result[-1]
        if audio is None:
            continue
        pcm = _to_pcm16(audio)
        if pcm:
            yield pcm, SAMPLE_RATE
