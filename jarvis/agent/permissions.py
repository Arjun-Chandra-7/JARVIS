"""Tool-permission gate for the Agent SDK's `can_use_tool` hook.

Jarvis uses Claude Code's built-in tools (Bash, Read, Write, Edit, Glob, Grep, WebSearch,
WebFetch, ...). This gate lets safe actions through automatically and asks for confirmation
before (a) a destructive shell command runs, or (b) Jarvis edits a note in the vault that you
authored (i.e. one without `author: jarvis` frontmatter). It is a heuristic, not a sandbox —
run Jarvis as a low-privilege user.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Awaitable, Callable, Optional

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

# Async callback that asks the user to approve an action; returns True to allow.
# It receives a short phrase describing the action, e.g. "run this command: ...".
ConfirmCallback = Callable[[str], Awaitable[bool]]

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Custom (mcp__jarvis__*) tools whose effects reach outside — confirm before running.
_CONFIRM_CUSTOM_TOOLS = {
    "mcp__jarvis__google_email_send",
    "mcp__jarvis__google_calendar_create",
    "mcp__jarvis__suspend_computer",
}


def _describe_custom(name: str, tool_input: dict) -> str:
    if name.endswith("google_email_send"):
        return f"send an email to {tool_input.get('to', '?')} — subject: {tool_input.get('subject', '')}"
    if name.endswith("google_calendar_create"):
        return f"create a calendar event '{tool_input.get('title', '')}' at {tool_input.get('start', '')}"
    if name.endswith("suspend_computer"):
        return "put the computer to sleep"
    return f"use the {name} tool"

# Heuristic patterns that mark a shell command as needing confirmation.
_DESTRUCTIVE = [
    r"\brm\b", r"\brmdir\b", r"\bmv\b", r"\bdd\b", r"\bshred\b", r"\btruncate\b",
    r"\bmkfs\w*", r"\bfdisk\b", r"\bparted\b", r"\bformat\b",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b",
    r"\bkill(all)?\b", r"\bpkill\b", r"\bchown\b", r"\bchmod\s+-R\b",
    r"\bsudo\b", r"(^|\s)su\s", r"\btee\b",
    r"\bgit\s+push\b", r"\bgit\s+reset\s+--hard\b", r"\bgit\s+clean\b",
    r"\bnpm\s+publish\b", r"\bpip\s+uninstall\b", r"\bapt(-get)?\s+(remove|purge)\b",
    r"\b(curl|wget|nc|netcat|ssh|scp|sftp|rsync)\b",  # data transmission & exfiltration
    r"\b(eval|exec)\b", r"base64\s+-d", r"\b(sh|bash|python|python3|perl|ruby)\s+-c\b",  # obfuscated execution
    r"\|\s*(sudo\s+)?(sh|bash|zsh|python|python3)\b", # curl ... | sh
    r":\(\)\s*\{",                            # fork bomb
    r"(?<![0-9&])>(?![>&])",                  # overwrite redirection (not >>, 2>&1, &>)
]
_DESTRUCTIVE_RE = re.compile("|".join(_DESTRUCTIVE))


def is_destructive(command: str) -> bool:
    return bool(_DESTRUCTIVE_RE.search(command))


def _is_jarvis_authored(path: Path) -> bool:
    try:
        head = path.read_text(errors="ignore")[:400]
    except OSError:
        return False
    return "author: jarvis" in head


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def make_permission_gate(
    confirm_fn: Optional[ConfirmCallback] = None,
    allow_unconfirmed: bool = False,
    vault_path: Optional[Path] = None,
):
    """Build an async `can_use_tool` callback for ClaudeAgentOptions."""
    vault = Path(vault_path).resolve() if vault_path else None

    async def _ask(description: str) -> bool:
        return bool(confirm_fn and await confirm_fn(description))

    async def can_use_tool(name: str, tool_input: dict, context: ToolPermissionContext):
        if name == "Bash":
            command = tool_input.get("command", "")
            if is_destructive(command) and not allow_unconfirmed:
                if not await _ask(f"run this command:\n    {command}"):
                    return PermissionResultDeny(message="User declined this command.")

        elif name in EDIT_TOOLS and vault is not None:
            raw = tool_input.get("file_path") or tool_input.get("notebook_path")
            if raw:
                try:
                    target = Path(raw).resolve()
                except Exception:  # noqa: BLE001
                    target = None
                if (
                    target is not None
                    and _within(target, vault)
                    and target.exists()
                    and not _is_jarvis_authored(target)
                ):
                    rel = target.relative_to(vault)
                    if not await _ask(f"edit your note {rel} (which you wrote, not Jarvis)"):
                        return PermissionResultDeny(message="User declined editing their note.")

        elif name in _CONFIRM_CUSTOM_TOOLS:
            if not await _ask(_describe_custom(name, tool_input)):
                return PermissionResultDeny(message="User declined this action.")

        return PermissionResultAllow()

    return can_use_tool
