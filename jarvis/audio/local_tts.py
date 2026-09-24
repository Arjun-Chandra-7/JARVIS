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
from typing import Iterable, Iterator, Optional

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
    """Load the voice ahead of first use so the first reply isn't slow. True if a voice is ready.

    Whichever voice will actually speak. Warming Piper on a machine that has Kokoro would pay
    1.6 s at startup for a fallback and still leave Kokoro's second to be paid on the first
    reply — the exact delay this exists to remove.
    """
    if _better_voice_available():
        from . import kokoro_tts

        if kokoro_tts.warmup():
            return True
        # Kokoro is present but would not load, so Piper is about to be doing the talking.
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


def _better_voice_available() -> bool:
    """Whether Kokoro is installed with its weights on disk.

    Checked per call rather than cached: the weights can appear while Jarvis is running, and a
    cached "no" from boot would mean a restart before the better voice is heard.
    """
    try:
        from . import kokoro_tts

        return kokoro_tts.available()
    except Exception:  # noqa: BLE001
        return False


def synth(text: str, model_path: str) -> tuple[bytes, int]:
    """Synthesise the whole text at once. Kept for callers that want a single buffer."""
    text = (text or "").strip()
    if not text:
        return b"", _sample_rate(model_path)
    if _better_voice_available():
        from . import kokoro_tts

        try:
            return kokoro_tts.synth(text)
        except Exception:  # noqa: BLE001 - a voice that fails must not cost the reply
            pass
    voice = _get_voice(model_path)
    if voice is not None:
        return _synth_chunk_api(voice, text)
    return _synth_chunk_cli(text, model_path)


# Said once, the first time the backup voice speaks in place of the main one: silently switching
# to a less clear voice leaves the person wondering what went wrong with their ears.
FALLBACK_NOTICE = "My main voice isn't available, so I'm using the backup voice."
_HINDI_UNSAYABLE = "That part is in Hindi, and the backup voice can't say it. It's on screen."
_fallback = {"announced": False}


def _piper_stream(text: str, model_path: str, stop_event: Optional[threading.Event]):
    from .speech_text import language_of

    voice = _get_voice(model_path)
    if not _fallback["announced"] and _better_voice_available():
        # Kokoro is installed and failed: this is a fallback, not the configured voice.
        _fallback["announced"] = True
        text = f"{FALLBACK_NOTICE} {text}"
    said_unsayable = False
    for chunk in split_for_speech(text):
        if stop_event is not None and stop_event.is_set():
            return
        if language_of(chunk) != "en":
            # Piper here is English-only: Devanagari comes out as silence, Hinglish as English
            # misreadings. Say so once rather than mangle it.
            if said_unsayable:
                continue
            chunk, said_unsayable = _HINDI_UNSAYABLE, True
        if voice is not None:
            yield _synth_chunk_api(voice, chunk)
        else:
            yield _synth_chunk_cli(chunk, model_path)


def synth_stream(
    text: str, model_path: str, stop_event: Optional[threading.Event] = None,
    split_first: bool = True,
) -> Iterator[tuple[bytes, int]]:
    """Yield (pcm, sample_rate) per speakable chunk, synthesising as the caller consumes.

    Kokoro when it is there, Piper when it is not. The caller never chooses: every path into
    speech goes through here, so preferring the better voice in one place means the pill, the
    replies and the announcements all get it at once — and a machine without the weights keeps
    working exactly as before.
    """
    cached = _ACK_CACHE.get(text.strip())
    if cached is not None:
        yield cached
        return
    if _better_voice_available():
        from . import kokoro_tts

        emitted = False
        try:
            for item in kokoro_tts.synth_stream(text, stop_event=stop_event, split_first=split_first):
                emitted = True
                yield item
            _fallback["announced"] = False      # the main voice is back: say so again next time
            return
        except Exception:  # noqa: BLE001 - fall through to Piper rather than going silent
            if emitted:
                # Replaying the whole answer in Piper after Kokoro said its first sentence
                # sounds like Jarvis repeating himself. The next reply may use the fallback.
                return
    yield from _piper_stream(text, model_path, stop_event)


# Short lines said often, synthesised once at start-up: "Done, sir." should not wait on a model.
ACKNOWLEDGEMENTS = ("Done, sir.", "Give me a moment, sir. I'm working on it.",
                    "Give me a moment, sir. I'm checking.", "Alright, sir.", "Stopped, sir.",
                    "Cancelled, sir.", "Sorry sir, I didn't catch that.", "Paused, sir.",
                    "Playing, sir.", "Next, sir.", "Going back, sir.")
_ACK_CACHE: dict[str, tuple[bytes, int]] = {}


def warm_acknowledgements(model_path: str) -> int:
    """Synthesise the acknowledgements into memory. Returns how many are ready."""
    from .speech_text import with_honorific

    for line in ACKNOWLEDGEMENTS:
        spoken = with_honorific(line)
        if spoken in _ACK_CACHE:
            continue
        try:
            pcm, rate = synth(spoken, model_path)
        except Exception:  # noqa: BLE001 - a cache is an optimisation, never a requirement
            continue
        if pcm:
            _ACK_CACHE[spoken] = (pcm, rate)
    return len(_ACK_CACHE)


# --------------------------------------------------------------- speaking while it is written
# A reply is spoken only once the model has finished writing it, so the wait before Jarvis says
# anything includes the whole generation. For a three-sentence answer that is most of the wait,
# and none of it is visible — there is nothing on screen to explain the pause.
#
# The model emits fragments, not sentences: "Your first", " meeting is at", " ten." What follows
# turns that back into sentences and hands each one over the moment it is complete, so speech
# starts after the first sentence is written rather than after the last.

# Cut the opening on a clause as well, for the same reason kokoro_tts does: the first thing said
# should be short, because nothing can be heard until it is synthesised.
_CLAUSE_BREAK = re.compile(r",\s")
_FIRST_PIECE_CHARS = 48

# A full stop that is not the end of a sentence. Speaking "Dr" and then "Smith is waiting" as two
# utterances puts a breath in the middle of a name.
_NOT_AN_ENDING = re.compile(
    r"(?:\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|e\.g|i\.e|approx|fig|no)\.|"
    r"\b[A-Za-z]\.)\s*$", re.IGNORECASE)

_ENDS_A_SENTENCE = re.compile(r"[.!?।]['\")\]]?\s")


def sentences_as_they_arrive(deltas: Iterable[str]) -> Iterator[str]:
    """Whole sentences out of a stream of fragments, each yielded as soon as it is complete.

    Nothing is held back waiting for more: the point is to speak early. The tail left over when
    the stream ends is yielded too, finished or not, because a model that stops mid-sentence
    still said something.
    """
    buffer = ""
    first = True
    for delta in deltas:
        if not delta:
            continue
        buffer += delta
        while True:
            # Inside a code block nothing is a sentence: "x = 1. y = 2" would otherwise be spoken
            # in pieces. Wait for the fence to close; the normaliser then replaces the block.
            if buffer.count("```") % 2:
                break
            fence = buffer.rfind("```")
            if fence >= 0:
                # A closed block goes out whole, with whatever led into it; the normaliser
                # turns it into "The code is on screen."
                piece, buffer = buffer[:fence + 3], buffer[fence + 3:]
                first = False
                if piece.strip():
                    yield piece.strip()
                continue
            # The opening is allowed to break on a comma, once, and only while it is long enough
            # that waiting for the full stop would be the slower thing.
            if first and len(buffer) >= _FIRST_PIECE_CHARS:
                clause = _CLAUSE_BREAK.search(buffer, 0, _FIRST_PIECE_CHARS + 40)
                ending = _ENDS_A_SENTENCE.search(buffer)
                if clause and (ending is None or clause.end() < ending.end()):
                    piece, buffer = buffer[:clause.start() + 1], buffer[clause.end():]
                    first = False
                    if piece.strip():
                        yield piece.strip()
                    continue

            match = _ENDS_A_SENTENCE.search(buffer)
            if not match:
                break
            candidate = buffer[:match.end()]
            if _NOT_AN_ENDING.search(candidate):
                # An abbreviation, not an ending. Look past it for the real one rather than
                # cutting a name in half.
                later = _ENDS_A_SENTENCE.search(buffer, match.end())
                if not later:
                    break
                candidate = buffer[:later.end()]
                match = later
            buffer = buffer[match.end():]
            first = False
            if candidate.strip():
                yield candidate.strip()

    if buffer.strip():
        yield buffer.strip()


def speak_as_it_arrives(
    deltas: Iterable[str],
    model_path: str,
    output_device: int = -1,
    stop_event: Optional[threading.Event] = None,
    on_first_audio: Optional[callable] = None,
    on_level: Optional[callable] = None,
    on_sentence: Optional[callable] = None,
    on_played: Optional[callable] = None,
) -> str:
    """Speak a reply while it is still being written. Returns everything that was said.

    The same player as `speak`; only the source differs. The text is returned because the caller
    still needs the finished reply — to show it, to log it, to remember it — and it would
    otherwise have been consumed by the speaking.
    """
    said: list[str] = []

    def pieces() -> Iterator[str]:
        from .speech_text import is_filler, leaks_internals

        for sentence in sentences_as_they_arrive(deltas):
            if stop_event is not None and stop_event.is_set():
                return
            if leaks_internals(sentence) or is_filler(sentence):
                continue                  # a tool name, a greeting, an offer of help: never said
            said.append(sentence)
            if on_sentence is not None:
                # Here, and not around `deltas`: this is where whole sentences exist. Reporting
                # the fragments instead would be a notification per token.
                on_sentence(sentence)
            yield sentence

    def audio() -> Iterator[tuple]:
        from .speech_text import normalize

        for index, sentence in enumerate(pieces()):
            yield from _labelled(normalize(sentence), sentence, model_path, stop_event, index == 0)

    _play(audio(), output_device, stop_event, on_first_audio, on_level, on_played)
    return " ".join(said)


def _labelled(spoken: str, label: str, model_path: str, stop_event, first: bool) -> Iterator[tuple]:
    """(pcm, rate, label) for one sentence; the label is attached to its last chunk only, so
    `on_played` fires once the whole sentence has actually been heard."""
    if not spoken.strip():
        return
    last = None
    for item in synth_stream(spoken, model_path, stop_event, split_first=first):
        if last is not None:
            yield (*last, None)
        last = item
    if last is not None:
        yield (*last, label)


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
    on_played: Optional[callable] = None,
) -> None:
    """Speak `text`, starting playback on the first sentence.

    A worker synthesises ahead while the main thread plays, so the gap between sentences is
    covered by the audio already queued. `stop_event` cuts playback promptly — it is checked
    between 4 KB writes, not just between sentences.
    """
    text = (text or "").strip()
    if not text:
        return
    if text in _ACK_CACHE:
        _play(iter([(*_ACK_CACHE[text], text)]), output_device, stop_event, on_first_audio,
              on_level, on_played)
        return

    def audio() -> Iterator[tuple]:
        for index, sentence in enumerate(sentences_as_they_arrive([text + " "])):
            if stop_event is not None and stop_event.is_set():
                return
            yield from _labelled(sentence, sentence, model_path, stop_event, index == 0)

    _play(audio(), output_device, stop_event, on_first_audio, on_level, on_played)


def speak_segments(
    segments: list[str],
    model_path: str,
    output_device: int = -1,
    stop_event: Optional[threading.Event] = None,
    on_first_audio: Optional[callable] = None,
    on_level: Optional[callable] = None,
    on_start: Optional[callable] = None,
    on_played: Optional[callable] = None,
) -> None:
    """Speak ``segments`` in order, each one a unit that something else is waiting on.

    For the teaching overlay: every segment is a phrase with a picture that belongs to it, so
    the segments are never merged or re-split into other sentences, and ``on_start(i, at_ms)``
    is called as segment *i*'s first audio is handed to the device — ``at_ms`` being the wall
    clock time it will actually be heard, the write time plus the stream's output latency. That
    is what the picture is scheduled against: the sound, not an estimate of when it might start.
    """
    from .speech_text import normalize

    def audio() -> Iterator[tuple]:
        for index, segment in enumerate(segments):
            if stop_event is not None and stop_event.is_set():
                return
            spoken = normalize(segment)
            first = True
            for item in _labelled(spoken, segment, model_path, stop_event, index == 0):
                yield (*item, index if first else None)
                first = False

    _play(audio(), output_device, stop_event, on_first_audio, on_level, on_played, on_start)


# When playback last actually stopped because it was asked to — what barge-in latency is
# measured against (speech onset → this).
PLAYBACK = {"stopped_at": 0.0}
BLOCK_S = 0.04     # written 40 ms at a time: a stop is noticed within one block


def _play(
    audio: Iterator[tuple],
    output_device: int = -1,
    stop_event: Optional[threading.Event] = None,
    on_first_audio: Optional[callable] = None,
    on_level: Optional[callable] = None,
    on_played: Optional[callable] = None,
    on_start: Optional[callable] = None,
) -> None:
    """Play (pcm, rate[, label[, start]]) items as they are produced.

    Split out of `speak` so that speaking a finished reply and speaking one still being written
    share a player rather than having two of them drift apart. A worker synthesises ahead while
    this thread plays, so the gap between sentences is covered by audio already queued.

    Every chunk is brought to one loudness and held under what the speakers can pass at their
    current volume (loudness.py). A label on an item is handed to `on_played` once that item has
    been played to the end — how an interrupted reply knows what was actually heard.

    Stopping aborts the device stream rather than draining it: `stop()` plays out whatever is
    buffered, which is exactly the half-second that makes an interruption feel ignored.
    """
    import sounddevice as sd

    from . import loudness

    pending: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=2)

    def produce() -> None:
        try:
            for item in audio:
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
    stream_rate = 0
    announced = False
    stopped = False
    gain = loudness.sink_gain()
    try:
        while True:
            # Waiting for the next sentence is still speaking: a stop must be noticed here too.
            # Found on the end-to-end run — an interruption in the gap between "Give me a moment"
            # and the answer waited 1.3 s for the answer's first sentence before stopping.
            try:
                item = pending.get(timeout=BLOCK_S)
            except queue.Empty:
                if stop_event is not None and stop_event.is_set():
                    stopped = True
                    break
                continue
            if item is None:
                break
            pcm, rate = item[0], item[1]
            label = item[2] if len(item) > 2 else None
            start = item[3] if len(item) > 3 else None
            if not pcm:
                continue
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            if stream is not None and rate != stream_rate:
                # The fallback voice speaks at another rate: a stream is one rate.
                stream.stop()
                stream.close()
                stream = None
            if stream is None:
                stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16",
                                            device=device, latency="low")
                stream.start()
                stream_rate = rate
            if not announced:
                announced = True
                if on_first_audio is not None:
                    try:
                        on_first_audio()
                    except Exception:  # noqa: BLE001
                        pass
            if start is not None and on_start is not None:
                # Heard once what is already queued ahead of it has played: the stream's output
                # latency, which is also how far ahead of the speaker these writes run.
                try:
                    ahead = float(getattr(stream, "latency", 0.0) or 0.0)
                    on_start(start, (__import__("time").time() + ahead) * 1000.0)
                except Exception:  # noqa: BLE001 — a picture must never stop the voice
                    pass
            pcm = loudness.shape(pcm, gain)
            chunk = max(512, int(rate * BLOCK_S) * 2)
            for i in range(0, len(pcm), chunk):
                if stop_event is not None and stop_event.is_set():
                    stopped = True
                    break
                block = pcm[i : i + chunk]
                if on_level is not None:
                    on_level(_peak(block))
                stream.write(block)
            if stopped:
                break
            if label is not None and on_played is not None:
                try:
                    on_played(label)
                except Exception:  # noqa: BLE001
                    pass
    finally:
        if stream is not None:
            if stopped:
                stream.abort()
            else:
                stream.stop()
            stream.close()
        if stopped:
            PLAYBACK["stopped_at"] = __import__("time").monotonic()
        # Drain so the producer thread can finish instead of blocking on a full queue.
        try:
            while pending.get_nowait() is not None:
                pass
        except queue.Empty:
            pass
