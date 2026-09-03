"""Background autonomous tasks.

'Jarvis, go research X and tell me when you're done' — the interactive session stays responsive
while a separate agent works the task to completion, writes the result into the vault, and fires a
notification.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import Config
from ..memory import vault as vaultmod
from .notify import notify


@dataclass
class Job:
    id: str
    description: str
    status: str = "queued"  # queued | running | done | error | rate_limited
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

    def dispatch_antigravity(
        self,
        raw_prompt: str,
        folder: Optional[str] = None,
        active_file: Optional[str] = None,
        active_file_path: Optional[str] = None,
    ) -> str:
        """Dispatch a coding prompt to Antigravity CLI (agy) in the background with prompt refinement.

        Runs purely via CLI on the exact workspace and file where the user is in VS Code.
        """
        self._counter += 1
        job = Job(id=f"agy-{self._counter}", description=raw_prompt)
        self.jobs[job.id] = job
        asyncio.create_task(self._run_antigravity_job(job, folder, active_file, active_file_path))
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
            from ..agent.autonomous import is_rate_limited

            job.status = "rate_limited" if is_rate_limited(job.result) else "done"
        except Exception as exc:  # noqa: BLE001 - record any failure on the job
            job.status = "error"
            job.result = f"{type(exc).__name__}: {exc}"
        job.finished = datetime.now()
        self._write_result(job)
        headline = job.result[:200] if job.status == "done" else f"[{job.status}] {job.result[:150]}"
        notify(f"Jarvis finished: {job.description[:50]}", headline, speak=True)

    async def _run_antigravity_job(
        self,
        job: Job,
        folder: Optional[str],
        active_file: Optional[str],
        active_file_path: Optional[str],
    ) -> None:
        from ..integrations import coding, prompt_engine

        # Auto-detect precision VS Code location if not provided
        ctx = coding.active_context()
        target_folder = folder or ctx.get("folder") or str(Path.cwd())
        target_file = active_file or ctx.get("file")
        target_file_path = active_file_path or ctx.get("file_path")
        project_name = Path(target_folder).name or "project"
        target_label = target_file or project_name

        # Refine voice prompt into structured Antigravity CLI brief anchored to the target file
        enhanced_brief, _ = prompt_engine.refine_prompt(
            raw_prompt=job.description,
            folder=target_folder,
            active_file=target_file,
            active_file_path=target_file_path,
            user_name=self.config.user_name,
        )

        job.status = "running"
        # Execute Antigravity CLI (agy) headlessly in the workspace
        try:
            res = await asyncio.to_thread(
                coding.run_agy_headless,
                target_folder,
                enhanced_brief,
                900,
            )
            if res.get("ok"):
                job.status = "done"
                diff_stat = res.get("diff_stat", "")
                stat_last = diff_stat.splitlines()[-1].strip() if diff_stat else "changes applied"
                job.result = (
                    f"Antigravity CLI completed task on {target_label}:\n"
                    f"{stat_last}\n\n"
                    f"{res.get('output', '')}"
                )
                spoken_alert = f"Sir, agy CLI has finished your task on {target_label}. {stat_last}."
            else:
                job.status = "error"
                err = res.get("stderr") or res.get("summary") or "Execution failed"
                job.result = f"Antigravity CLI error on {target_label}: {err}"
                spoken_alert = f"Sir, agy CLI encountered an error on {target_label}."
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.result = f"Antigravity CLI failed: {exc}"
            spoken_alert = f"Sir, agy CLI run failed on {target_label}: {exc}"

        job.finished = datetime.now()
        self._write_result(job)
        notify(f"agy finished: {target_label}", spoken_alert, speak=True)

    async def _run_agent(self, description: str) -> str:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
        )
        from ..agent.autonomous import background_gate

        task_system = (
            "You are Jarvis, working autonomously on a background task for {user} while they are away. "
            "Do the work end to end with your tools — search and fetch the web, read and write files, "
            "run safe shell commands. When finished, give a concise result."
        )

        options = ClaudeAgentOptions(
            system_prompt=task_system.format(user=self.config.user_name),
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
        try:
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
        except Exception:
            pass
