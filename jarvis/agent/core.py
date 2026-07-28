"""The agent core: Jarvis's brain, driven by Claude Code via the Claude Agent SDK.

Runs on your Claude subscription (no API key). Claude Code's built-in tools (Bash, Read, Write,
Edit, Glob, Grep, WebSearch, WebFetch) provide computer control, vault access, and web; the
permission gate in `permissions.py` confirms destructive shell commands. The Obsidian vault is
Jarvis's memory: its profile is pinned into the system prompt, and every turn auto-commits.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from ..config import Config
from ..jobs.runner import JobRunner
from ..memory import vault as vaultmod
from .permissions import make_permission_gate
from .prompt import build_system_prompt
from .sdk_tools import build_tool_server

ToolCallback = Callable[[str, str], None]
ConfirmCallback = Callable[[str], Awaitable[bool]]


def _tool_detail(name: str, tool_input: dict) -> str:
    """A short human-readable summary of a tool call, for status output."""
    for key in ("command", "file_path", "path", "query", "url", "pattern"):
        if key in tool_input:
            return str(tool_input[key])
    return ", ".join(f"{k}={v}" for k, v in list(tool_input.items())[:2])


class JarvisAgent:
    def __init__(
        self,
        config: Config,
        mode: str = "text",
        confirm_fn: Optional[ConfirmCallback] = None,
        on_tool: Optional[ToolCallback] = None,
    ) -> None:
        self.config = config
        self.mode = mode
        self.on_tool: ToolCallback = on_tool or (lambda name, detail: None)
        self.last_result: Optional[ResultMessage] = None

        self.job_runner = JobRunner(config)
        self._tool_server = build_tool_server(config, self.job_runner)
        self._gate = make_permission_gate(
            confirm_fn, config.allow_unconfirmed_shell, vault_path=config.vault_path
        )
        self.options = self._build_options()
        self._client: Optional[ClaudeSDKClient] = None

    def _build_options(self) -> ClaudeAgentOptions:
        """Assemble options from the *current* config (re-read on model/effort switches)."""
        config = self.config
        profile_text = vaultmod.read_profile(config.vault_path)
        journal_text = vaultmod.read_today_journal(config.vault_path)
        disallowed = [] if config.enable_web else ["WebSearch", "WebFetch"]
        return ClaudeAgentOptions(
            system_prompt=build_system_prompt(
                config.user_name,
                self.mode,
                vault_path=config.vault_path,
                profile_text=profile_text,
                journal_text=journal_text,
            ),
            permission_mode="default",
            can_use_tool=self._gate,
            model=config.model_or_none,
            effort=config.effort,  # type: ignore[arg-type]
            disallowed_tools=disallowed,
            cwd=str(Path.home()),                       # anchor computer control at home
            add_dirs=[str(config.vault_path)],          # grant vault access to file tools
            mcp_servers={"jarvis": self._tool_server},  # recall, background tasks, screen capture
            setting_sources=None,                       # isolate: no CLAUDE.md / project settings
        )

    async def _apply_pending_settings(self) -> None:
        """If a tool changed the model/effort, rebuild options and reconnect the client."""
        from . import runtime

        if self._client is None or not runtime.take_dirty():
            return
        await self._client.disconnect()
        self.options = self._build_options()
        self._client = ClaudeSDKClient(options=self.options)
        await self._client.connect()

    async def __aenter__(self) -> "JarvisAgent":
        self._client = ClaudeSDKClient(options=self.options)
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.disconnect()

    @staticmethod
    def _clean_for_log(user_text: str) -> str:
        """Strip injected [context] prefix lines and truncate, for the episodic journal entry."""
        lines = user_text.splitlines()
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("[")):
            lines.pop(0)
        text = " ".join(" ".join(lines).split())
        if len(text) > 140:
            text = text[:137] + "…"
        return text

    async def send(self, user_text: str) -> str:
        """Send one user turn; run the tool loop to completion; return the reply text."""
        assert self._client is not None, "JarvisAgent must be used as an async context manager"
        await self._apply_pending_settings()  # honour any voice-requested model/effort switch

        # Episodic memory: log the request to today's journal with a timestamp.
        clean = self._clean_for_log(user_text)
        if clean:
            try:
                vaultmod.journal_append(self.config.vault_path, clean)
            except Exception:  # noqa: BLE001
                pass

        # Time-awareness: prepend the current time so Jarvis knows "now" and how much time passed.
        now = datetime.now().astimezone()
        stamped = f"[current time: {now:%A %Y-%m-%d %H:%M %Z}]\n{user_text}"
        await self._client.query(stamped)

        parts: list[str] = []
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        self.on_tool(block.name, _tool_detail(block.name, block.input))
            elif isinstance(message, ResultMessage):
                self.last_result = message
                break

        # Persist any memory changes the turn made.
        vaultmod.git_autocommit(
            self.config.vault_path,
            f"jarvis: memory update {datetime.now():%Y-%m-%d %H:%M}",
        )

        reply = "".join(parts).strip()
        if not reply and self.last_result is not None and self.last_result.result:
            reply = self.last_result.result.strip()
        return reply
