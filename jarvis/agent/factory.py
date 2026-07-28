"""Pick the brain backend based on config.brain ('groq' or 'claude'). Both share one interface."""

from __future__ import annotations

from ..config import Config


def make_agent(config: Config, mode: str = "text", confirm_fn=None, on_tool=None):
    if config.brain == "chatgpt":  # ChatGPT web app as the brain (no API key)
        from .chatgpt_core import ChatGPTAgent

        return ChatGPTAgent(config, mode, confirm_fn, on_tool)
    if config.brain in ("groq", "gemini", "ollama"):  # OpenAI-compatible brains
        from .groq_core import GroqAgent

        return GroqAgent(config, mode, confirm_fn, on_tool)
    from .core import JarvisAgent

    return JarvisAgent(config, mode, confirm_fn, on_tool)
