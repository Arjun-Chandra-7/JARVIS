"""Runtime configuration, loaded from environment / .env.

Jarvis's brain is ChatGPT via the web app (JARVIS_BRAIN=chatgpt, the default) — the user's own
account, no API key; sign in once with `--chatgpt-login`. Alternative API brains: gemini / groq.
Voice (Phase 5) needs Deepgram (STT), ElevenLabs (TTS), and Picovoice (wake word) keys.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # always the repo's .env, any CWD
except ImportError:  # dotenv is optional; env vars still work without it
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    # --- brain ---
    # JARVIS_BRAIN = "chatgpt" (default — your ChatGPT account via the web app, no API key),
    # "gemini" (API key), or "groq" (API key). Ollama and Claude have been removed.
    brain: str = field(default_factory=lambda: os.environ.get("JARVIS_BRAIN", "chatgpt").lower())
    model: str = field(default_factory=lambda: os.environ.get("JARVIS_MODEL", "sonnet"))
    effort: str = field(default_factory=lambda: os.environ.get("JARVIS_EFFORT", "low"))
    user_name: str = field(default_factory=lambda: os.environ.get("JARVIS_USER_NAME", "sir"))

    # --- Groq (OpenAI-compatible) ---
    groq_api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    groq_model: str = field(
        default_factory=lambda: os.environ.get("JARVIS_GROQ_MODEL", "llama-3.1-8b-instant")
    )
    # Set true only if the chosen Groq model accepts images (e.g. a llama-4 vision model). The
    # default text model can't see screenshots, so we skip image injection to avoid API errors.
    groq_vision: bool = field(default_factory=lambda: _bool("JARVIS_GROQ_VISION", False))

    # --- Gemini (OpenAI-compatible endpoint; generous free tier + native vision) ---
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.environ.get("JARVIS_GEMINI_MODEL", "gemini-2.0-flash"))

    # --- image understanding (screen vision; optional) ---
    # "auto"/"gemini" use Gemini (needs GEMINI_API_KEY); the local moondream path stays as a
    # graceful fallback only if you happen to run it. "none" disables vision.
    vision_provider: str = field(default_factory=lambda: os.environ.get("JARVIS_VISION", "auto").lower())
    ollama_vision_model: str = field(default_factory=lambda: os.environ.get("JARVIS_VISION_MODEL", "moondream"))

    # --- capabilities ---
    enable_web: bool = field(default_factory=lambda: _bool("JARVIS_ENABLE_WEB", True))
    allow_unconfirmed_shell: bool = field(
        default_factory=lambda: _bool("JARVIS_ALLOW_UNCONFIRMED_SHELL", False)
    )

    # --- memory ---
    vault_path: Path = field(
        default_factory=lambda: Path(os.environ.get("JARVIS_VAULT", "~/JarvisVault")).expanduser()
    )

    # --- voice (Phase 5) ---
    picovoice_access_key: str = field(default_factory=lambda: os.environ.get("PICOVOICE_ACCESS_KEY", ""))
    deepgram_api_key: str = field(default_factory=lambda: os.environ.get("DEEPGRAM_API_KEY", ""))
    elevenlabs_api_key: str = field(default_factory=lambda: os.environ.get("ELEVENLABS_API_KEY", ""))
    wake_keyword: str = field(default_factory=lambda: os.environ.get("JARVIS_WAKE_KEYWORD", "jarvis"))
    stt_model: str = field(default_factory=lambda: os.environ.get("JARVIS_STT_MODEL", "nova-3"))
    # ElevenLabs "Rachel" is a sensible default public voice; override with your own.
    tts_voice_id: str = field(
        default_factory=lambda: os.environ.get("JARVIS_TTS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    )
    tts_model_id: str = field(
        default_factory=lambda: os.environ.get("JARVIS_TTS_MODEL_ID", "eleven_turbo_v2_5")
    )
    # end-of-utterance: trailing silence (ms) that ends a spoken phrase
    silence_ms: int = field(default_factory=lambda: _int("JARVIS_SILENCE_MS", 2000))
    max_utterance_s: int = field(default_factory=lambda: _int("JARVIS_MAX_UTTERANCE_S", 30))
    follow_up_s: int = field(default_factory=lambda: _int("JARVIS_FOLLOW_UP_S", 6))
    enable_barge_in: bool = field(default_factory=lambda: _bool("JARVIS_BARGE_IN", True))  # talk over him to cut him off
    screen_always: bool = field(default_factory=lambda: _bool("JARVIS_SCREEN_ALWAYS", False))  # keep screen vision on from start
    enable_followup: bool = field(default_factory=lambda: _bool("JARVIS_FOLLOWUP", False))  # keep listening after a reply (no wake word)
    afk_minutes: int = field(default_factory=lambda: _int("JARVIS_AFK_MIN", 15))  # idle time before a welcome-back brief
    city: str = field(default_factory=lambda: os.environ.get("JARVIS_CITY", "Bangalore"))  # for weather
    # 0 = auto-calibrate from ambient noise at startup; else an explicit RMS threshold
    vad_threshold: int = field(default_factory=lambda: _int("JARVIS_VAD_THRESHOLD", 0))
    audio_input_device: int = field(default_factory=lambda: _int("JARVIS_INPUT_DEVICE", -1))
    audio_output_device: int = field(default_factory=lambda: _int("JARVIS_OUTPUT_DEVICE", -1))

    # local (keyless) voice backend: openWakeWord + Whisper + Piper
    voice_backend: str = field(default_factory=lambda: os.environ.get("JARVIS_VOICE_BACKEND", "auto"))
    whisper_model: str = field(default_factory=lambda: os.environ.get("JARVIS_WHISPER_MODEL", "small.en"))
    whisper_beam: int = field(default_factory=lambda: _int("JARVIS_WHISPER_BEAM", 1))  # 1=fast, 5=accurate
    piper_model: str = field(
        default_factory=lambda: os.environ.get(
            "JARVIS_PIPER_MODEL",
            str(Path("~/.local/share/jarvis/piper/en_GB-alan-medium.onnx").expanduser()),
        )
    )
    wake_threshold: float = field(default_factory=lambda: float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.7")))

    # --- proactive routines (Phase 4) ---
    enable_brief: bool = field(default_factory=lambda: _bool("JARVIS_ENABLE_BRIEF", True))
    brief_time: str = field(default_factory=lambda: os.environ.get("JARVIS_BRIEF_TIME", "07:00"))

    # --- Google integration (Phase 3) ---
    google_client_secret: Path = field(
        default_factory=lambda: Path(
            os.environ.get("GOOGLE_CLIENT_SECRET_FILE", "~/.config/jarvis/client_secret.json")
        ).expanduser()
    )

    @property
    def google_token_file(self) -> Path:
        return self.google_client_secret.parent / "google-token.json"

    # --- remote bridge (Telegram) ---
    telegram_bot_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram_allowed_chat_ids: frozenset = field(
        default_factory=lambda: frozenset(
            x.strip()
            for x in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
            if x.strip()
        )
    )

    # --- phone (KDE Connect) ---
    kde_device_id: str = field(default_factory=lambda: os.environ.get("JARVIS_KDE_DEVICE_ID", ""))

    def brief_hm(self) -> tuple[int, int]:
        try:
            hour, minute = self.brief_time.split(":")
            return int(hour), int(minute)
        except Exception:  # noqa: BLE001
            return 7, 0

    @property
    def model_or_none(self) -> str | None:
        return self.model or None

    def llm_params(self) -> tuple[str, str, str]:
        """(base_url, api_key, model) for the OpenAI-compatible API brains (gemini/groq)."""
        if self.brain == "gemini":
            return ("https://generativelanguage.googleapis.com/v1beta/openai/",
                    self.gemini_api_key, self.gemini_model)
        return ("https://api.groq.com/openai/v1", self.groq_api_key, self.groq_model)

    def brain_has_vision(self) -> bool:
        return self.brain == "gemini"

    def require_claude_cli(self) -> str:
        """Preflight the selected brain. (Name kept for callers; no Claude/Ollama anymore.)"""
        # ChatGPT brain drives the web app via the user's account — no key/CLI, just a one-time login.
        if self.brain == "chatgpt":
            try:
                import playwright  # noqa: F401
            except ImportError:
                raise SystemExit(
                    "ChatGPT brain selected but Playwright isn't installed.\n"
                    "  .venv/bin/pip install playwright\n"
                    "  then sign in once:  python -m jarvis --chatgpt-login"
                )
            return "chatgpt"
        if self.brain == "gemini":
            if not self.gemini_api_key:
                raise SystemExit(
                    "Gemini brain selected but GEMINI_API_KEY is empty.\n"
                    "  Get a free key at https://aistudio.google.com/apikey and put it in .env:\n"
                    "  GEMINI_API_KEY=...   (or set JARVIS_BRAIN=chatgpt / groq)."
                )
            return "gemini"
        if self.brain == "groq":
            if not self.groq_api_key:
                raise SystemExit(
                    "Groq brain selected but GROQ_API_KEY is empty.\n"
                    "  Put your key in .env:  GROQ_API_KEY=gsk_...   (or set JARVIS_BRAIN=chatgpt)."
                )
            return "groq"
        raise SystemExit(
            f"Unknown JARVIS_BRAIN={self.brain!r}. Use 'chatgpt' (default), 'gemini', or 'groq'."
        )

    def resolved_voice_backend(self) -> str:
        """'local' (keyless) or 'cloud' (keys). 'auto' picks cloud when all keys are present."""
        if self.voice_backend in ("local", "cloud"):
            return self.voice_backend
        return "cloud" if not self.missing_voice_keys() else "local"

    def missing_voice_keys(self) -> list[str]:
        missing = []
        if not self.picovoice_access_key:
            missing.append("PICOVOICE_ACCESS_KEY (wake word — console.picovoice.ai)")
        if not self.deepgram_api_key:
            missing.append("DEEPGRAM_API_KEY (speech-to-text — deepgram.com)")
        if not self.elevenlabs_api_key:
            missing.append("ELEVENLABS_API_KEY (text-to-speech — elevenlabs.io)")
        return missing


CONFIG = Config()
