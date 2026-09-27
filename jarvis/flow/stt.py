"""Speech to text for dictation: a primary and a fallback, each with a deadline.

JARVIS_DICTATION_STT is the order to try, comma separated (default "groq,local"):

    groq    Whisper large-v3-turbo on Groq — the key is already configured for the brain
    local   faster-whisper on this machine (GPU when Ollama leaves room, else CPU)

Language is detected per utterance (JARVIS_DICTATION_LANGUAGE=auto): Hindi comes back in
Devanagari, English in Latin letters, and Hinglish as the recogniser hears it. Nothing is
translated. The personal dictionary is passed as vocabulary so names are heard as names.

Audio exists only in memory here; nothing is written to disk.
"""
from __future__ import annotations

import io
import os
import time
import wave
from dataclasses import dataclass, field
from typing import Callable, Optional

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


@dataclass
class Heard:
    text: str = ""
    provider: str = ""
    seconds: float = 0.0
    language: str = ""
    failures: list[str] = field(default_factory=list)


def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def groq(pcm: bytes, rate: int = 16000, vocabulary: str = "", language: str = "auto",
         timeout: float = 8.0, key: Optional[str] = None) -> tuple[str, str]:
    import httpx
    key = key or os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise RuntimeError("no Groq key")
    data = {"model": os.environ.get("JARVIS_DICTATION_GROQ_MODEL", "whisper-large-v3-turbo"),
            "response_format": "verbose_json", "temperature": "0"}
    if vocabulary:
        data["prompt"] = vocabulary[:800]
    if language and language != "auto":
        data["language"] = language
    r = httpx.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, data=data,
                   files={"file": ("speech.wav", _wav(pcm, rate), "audio/wav")}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    return str(body.get("text") or "").strip(), str(body.get("language") or "")


_HINDI_FAMILY = {"hi", "ur", "mr", "ne", "sa", "pa", "gu", "bn"}

# Measured on Groq Whisper: with no prompt Hindi speech comes back in Devanagari and the English
# words inside it are spelled in Devanagari too ("इलेक्ट्रिसिती"). Primed with a line of Roman
# Hinglish it writes "Aaj electricity ka chapter revise karna hai" — Hindi in Roman letters, the
# English words as English. English speech is unaffected.
ROMAN_HINGLISH_HINT = "Haan theek hai. Kal maths ka homework submit karna hai. Aaj science ka chapter revise karna hai."


def hindi_script(profile: str) -> str:
    """roman or devanagari, for this field. JARVIS_DICTATION_HINDI_SCRIPT: profile|roman|devanagari."""
    choice = os.environ.get("JARVIS_DICTATION_HINDI_SCRIPT", "profile").strip().lower()
    if choice in {"roman", "devanagari"}:
        return choice
    return "roman" if profile in {"messaging", "search"} else "devanagari"


SHELL_HINT = "git status, git commit, ls -la, cd, sudo apt, python3, pip install, npm run, grep, cat, mkdir"


def prompt_for(vocabulary: str, script: str, profile: str = "") -> str:
    if profile in {"terminal", "code"}:
        return f"{SHELL_HINT}, {vocabulary}".strip(", ")
    return f"{ROMAN_HINGLISH_HINT} {vocabulary}".strip() if script == "roman" else vocabulary


def local(pcm: bytes, rate: int = 16000, vocabulary: str = "", language: str = "auto",
          timeout: float = 20.0, model: Optional[str] = None) -> tuple[str, str]:
    """faster-whisper, tuned for dictation rather than commands.

    The assistant's local path always primes the decoder with an English command vocabulary and
    drops low-confidence segments. Measured on Hindi speech, that turned "आज मुझे विज्ञान का
    अध्याय दोहराना है" into "Today I want to learn about science" — a translation — or nothing at
    all. Here the language is detected first, the vocabulary is only a prompt for English, and
    segments are dropped only when Whisper says they are not speech.
    """
    from ..audio import local_stt
    from ..config import CONFIG
    # "small": measured, "base" writes Hindi speech in Urdu script; "medium" only fits on the
    # processor here (Ollama holds the GPU) and took ~100 s a sentence.
    name = model or os.environ.get("JARVIS_DICTATION_LOCAL_MODEL", "small")
    audio = local_stt._to_float32(pcm, rate)
    lang = None if language in ("", "auto") else language
    if lang is None:
        try:
            detected, _prob, _all = local_stt._get_model(name).detect_language(audio)
            lang = "hi" if detected in _HINDI_FAMILY else detected
        except Exception:  # noqa: BLE001 — let transcribe decide
            lang = None
    segments, info = local_stt._transcribe(
        name, audio, language=lang, task="transcribe",
        initial_prompt=vocabulary if (vocabulary and (lang in (None, "en") or ROMAN_HINGLISH_HINT in vocabulary)) else None,
        beam_size=max(1, CONFIG.whisper_beam), vad_filter=True, condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segments if getattr(s, "no_speech_prob", 0) < 0.7).strip()
    return text, lang or getattr(info, "language", "") or ""


PROVIDERS: dict[str, Callable] = {"groq": groq, "local": local}


def _echoes(text: str, vocabulary: str) -> bool:
    import re
    words = set(re.findall(r"[\w'ऀ-ॿ]+", text.lower()))
    vocab = set(re.findall(r"[\w'ऀ-ॿ]+", vocabulary.lower()))
    return len(words) >= 2 and words <= vocab


def order() -> list[str]:
    raw = os.environ.get("JARVIS_DICTATION_STT", "groq,local")
    names = [n.strip().lower() for n in raw.split(",") if n.strip().lower() in PROVIDERS]
    return names or ["groq", "local"]


def transcribe(pcm: bytes, rate: int = 16000, vocabulary: str = "", language: Optional[str] = None,
               providers: Optional[list[str]] = None, table: Optional[dict] = None) -> Heard:
    table = table or PROVIDERS
    language = language or os.environ.get("JARVIS_DICTATION_LANGUAGE", "auto")
    out = Heard()
    for name in providers or order():
        fn = table.get(name)
        if fn is None:
            continue
        start = time.monotonic()
        try:
            text, lang = fn(pcm, rate, vocabulary, language)
        except Exception as exc:  # noqa: BLE001 — the next provider is tried; the reason is kept
            out.failures.append(f"{name}: {type(exc).__name__}")
            continue
        out.seconds = time.monotonic() - start
        if text and vocabulary and _echoes(text, vocabulary):
            out.failures.append(f"{name}: echoed the vocabulary")   # silence, read as the prompt
            continue
        if text:
            out.text, out.provider, out.language = text, name, lang
            return out
        out.failures.append(f"{name}: empty")
    return out
