import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.integrations.coding_jobs import CodingJobManager, build_command, normalize_selection, workspace_snapshot


class CodingJobsTest(unittest.TestCase):
    def test_selection_and_safe_commands(self):
        with tempfile.TemporaryDirectory() as root:
            choice = normalize_selection("use Claude with xhigh effort")
            self.assertEqual(choice["provider"], "claude")
            self.assertEqual(choice["effort"], "xhigh")
            codex = build_command("codex", "fix it", root, "gpt-5", "high")
            self.assertEqual(codex[:5], ["codex", "-c", 'model_reasoning_effort="high"', "--ask-for-approval", "on-request"])
            self.assertIn("workspace-write", codex)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex)
            self.assertEqual(build_command("agy", "fix", root, effort="max")[5], "high")

    def test_job_persists_final_status(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, state = Path(root), Path(root) / "state.json"
            async def fake_runner(command, cwd):
                self.assertEqual(command[0], "codex")
                self.assertEqual(cwd, str(workspace))
                return 0, "done", ""
            async def run():
                manager = CodingJobManager(state, runner=fake_runner)
                job = await manager.start("add a test", str(workspace))
                await manager._tasks[job["id"]]
                return job
            job = asyncio.run(run())
            saved = CodingJobManager(state).get_job(job["id"])
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["before"]["workspace"], str(workspace))
            self.assertTrue(workspace_snapshot(workspace)["exists"])

    def test_vscode_conversation_captures_context_then_launches(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, state = Path(root), Path(root) / "state.json"
            async def fake_runner(command, cwd):
                return 0, "done", ""
            async def run():
                manager = CodingJobManager(state, runner=fake_runner)
                ctx = {"folder": str(workspace), "file": "app.py"}
                with patch("jarvis.integrations.coding.active_context", return_value=ctx):
                    first = await manager.handle_message("fix the login bug in VS Code", "voice")
                    self.assertIn("provider", first.lower())
                    self.assertIn("model", (await manager.handle_message("codex", "voice")).lower())
                    self.assertIn("effort", (await manager.handle_message("default", "voice")).lower())
                    final = await manager.handle_message("xhigh", "voice")
                    self.assertIn("Prompt given, sir", final)
                    job_id = manager.list_jobs()[0]["id"]
                    await manager._tasks[job_id]

                    # Same folder again -> no provider/model/effort dialogue, launches straight away.
                    again = await manager.handle_message("also add a test in VS Code", "voice")
                self.assertIn("Prompt given, sir", again)
                self.assertNotIn("provider", again.lower())
                self.assertEqual(len(manager.list_jobs()), 2)
                for jid in list(manager._tasks):
                    await manager._tasks[jid]
            asyncio.run(run())
            statuses = {j["status"] for j in CodingJobManager(state).list_jobs()}
            self.assertEqual(statuses, {"completed"})

    def test_external_event_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root) / "state.json"
            manager = CodingJobManager(state)
            job = manager.create_external("review", root, "claude", external_id="remote-1")
            manager.record_event(job["id"], "completed", "provider webhook complete", "all clear")
            restored = CodingJobManager(state).get_job("remote-1")
            self.assertEqual(restored["status"], "completed")
            self.assertEqual(restored["output"], "all clear")

    def test_two_managers_merge_concurrent_job_updates(self):
        """Models the web process and a worker each updating the same durable state file."""
        with tempfile.TemporaryDirectory() as root:
            state = Path(root) / "state.json"
            async def runner(command, cwd):
                await asyncio.sleep(0.01)
                return 0, command[-1], ""
            async def run():
                first = CodingJobManager(state, runner=runner)
                second = CodingJobManager(state, runner=runner)
                jobs = await asyncio.gather(
                    first.start("job one", root), second.start("job two", root),
                )
                await asyncio.gather(first._tasks[jobs[0]["id"]], second._tasks[jobs[1]["id"]])
            asyncio.run(run())
            restored = CodingJobManager(state).list_jobs()
            self.assertEqual(len(restored), 2)
            self.assertEqual({job["status"] for job in restored}, {"completed"})


if __name__ == "__main__":
    unittest.main()
