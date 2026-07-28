"""Background autonomous tasks.

'Jarvis, go research X and tell me when you're done' — the interactive session stays responsive
while a *separate* Claude agent works the task to completion at higher effort, writes the result
into the vault, and fires a notification. Since no one is present to confirm, the background gate
blocks destructive shell commands outright.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from ..agent.autonomous import background_gate, is_rate_limited
from ..config import Config
from ..memory import vault as vaultmod
from .notify import notify

_TASK_SYSTEM = """\
You are Jarvis, working autonomously on a background task for {user} while they are away. Do the
work end to end with your tools — search and fetch the web, read and write files, run safe shell
commands. You cannot ask questions, so make reasonable assumptions and note them. When finished,
give a concise result: what you found or did, key details, and any caveats. If the task involves
research or a deliverable, also write it into the vault where it belongs (a project note, or
Jarvis/tasks/)."""


@dataclass
class Job:
    id: str
    description: str
    status: str = "queued"  # queued | running | done | error
    result: str = ""
    created: datetime = field(default_factory=datetime.now)
    finished: Optional[datetime] = None


class JobRunner:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.jobs: dict[str, Job] = {}
        self._counter = 0

    def dispatch(self, description: str) -> str:
        self._counter += 1
        job = Job(id=f"task-{self._counter}", description=description)
        self.jobs[job.id] = job
        asyncio.create_task(self._run(job))
        return job.id

    def status_report(self) -> str:
        if not self.jobs:
            return "No background tasks."
        lines = []
        for job in self.jobs.values():
            line = f"- {job.id} [{job.status}] {job.description[:70]}"
            if job.status == "done":
                line += f"\n    → {job.result[:400]}"
            elif job.status == "error":
                line += f"\n    ✗ {job.result[:200]}"
            lines.append(line)
        return "\n".join(lines)

    async def _run(self, job: Job) -> None:
        job.status = "running"
        try:
            job.result = await self._run_agent(job.description)
            job.status = "rate_limited" if is_rate_limited(job.result) else "done"
        except Exception as exc:  # noqa: BLE001 - record any failure on the job
            job.status = "error"
            job.result = f"{type(exc).__name__}: {exc}"
        job.finished = datetime.now()
        self._write_result(job)
        headline = job.result[:200] if job.status == "done" else f"[{job.status}] {job.result[:150]}"
        notify(f"Jarvis finished: {job.description[:50]}", headline)

    async def _run_agent(self, description: str) -> str:
        options = ClaudeAgentOptions(
            system_prompt=_TASK_SYSTEM.format(user=self.config.user_name),
            permission_mode="default",
            can_use_tool=background_gate,
            model=self.config.model_or_none,
            effort="high",
            cwd=str(Path.home()),
            add_dirs=[str(self.config.vault_path)],
            setting_sources=None,
        )
        parts: list[str] = []
        result_text: Optional[str] = None
        async with ClaudeSDKClient(options=options) as client:
            await client.query(description)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    result_text = message.result
                    break
        return "".join(parts).strip() or (result_text or "").strip() or "(no output)"

    def _write_result(self, job: Job) -> None:
        slug = re.sub(r"[^a-z0-9]+", "-", job.description.lower())[:40].strip("-") or "task"
        stamp = job.created.strftime("%Y%m%d-%H%M%S")
        path = self.config.vault_path / "Jarvis" / "tasks" / f"{stamp}-{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        finished = job.finished.isoformat(timespec="seconds") if job.finished else ""
        path.write_text(
            f"---\n"
            f"author: jarvis\n"
            f"type: task\n"
            f"status: {job.status}\n"
            f"created: {job.created.isoformat(timespec='seconds')}\n"
            f"finished: {finished}\n"
            f"---\n"
            f"# Background task: {job.description}\n\n"
            f"{job.result}\n"
        )
        vaultmod.git_autocommit(
            self.config.vault_path, f"jarvis: background {job.id} ({job.status})"
        )
