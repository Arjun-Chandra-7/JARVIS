"""Pick the brain backend based on config.brain. All backends share one send() interface.

Brains: 'chatgpt' (default — the user's ChatGPT account via the web app, no API key),
'gemini' / 'groq' (OpenAI-compatible API brains). Ollama and Claude have been removed.
"""

from __future__ import annotations

from ..config import Config


def make_agent(config: Config, mode: str = "text", confirm_fn=None, on_tool=None):
    if config.brain == "chatgpt":  # ChatGPT web app as the brain (no API key)
        from .chatgpt_core import ChatGPTAgent

        return ChatGPTAgent(config, mode, confirm_fn, on_tool)
    if config.brain in ("gemini", "groq", "ollama"):  # OpenAI-compatible brains (cloud or local)
        from .groq_core import GroqAgent

        return GroqAgent(config, mode, confirm_fn, on_tool)
    raise SystemExit(
        f"Unknown JARVIS_BRAIN={config.brain!r}. Use 'chatgpt', 'gemini', 'groq', or 'ollama'."
    )
