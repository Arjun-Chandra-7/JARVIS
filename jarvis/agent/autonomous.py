"""Shared helpers for autonomous (no-human) agent runs: background tasks and routines.

These spawn a fresh `ClaudeSDKClient` for a one-shot job. Since nobody is present to approve, the
gate blocks destructive shell outright. Also detects the subscription's rate-limit message so
callers can handle it gracefully instead of treating it as a real answer.
"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
)

from ..config import Config
from .permissions import is_destructive

_RATE_LIMIT_HINTS = ("session limit", "usage limit", "rate limit", "reached your limit", "resets ")


def is_rate_limited(text: str) -> bool:
    low = (text or "").lower()
    return any(hint in low for hint in _RATE_LIMIT_HINTS)


async def background_gate(name, tool_input, context):
    """Autonomous permission gate: allow everything except destructive shell."""
    if name == "Bash" and is_destructive(tool_input.get("command", "")):
        return PermissionResultDeny(message="Destructive shell blocked in autonomous mode.")
    return PermissionResultAllow()


async def run_once(config: Config, prompt: str, *, system: str, effort: str = "medium") -> str:
    """Run a single autonomous agent turn to completion and return its text."""
    options = ClaudeAgentOptions(
        system_prompt=system,
        permission_mode="default",
        can_use_tool=background_gate,
        model=config.model_or_none,
        effort=effort,  # type: ignore[arg-type]
        cwd=str(Path.home()),
        add_dirs=[str(config.vault_path)],
        setting_sources=None,
    )
    parts: list[str] = []
    result_text = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
            elif isinstance(message, ResultMessage):
                result_text = message.result
                break
    return "".join(parts).strip() or (result_text or "").strip() or ""
