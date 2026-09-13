"""Memory, recall, machine state and looking at the screen.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool

@tool("recall", "Search the Obsidian memory vault for relevant notes.", {"query": {"type": "string"}}, ["query"],
          parallel_safe=True)
async def recall(ctx, a):
    from ..memory import search as memsearch
    return memsearch.recall(a.get("query", ""), ctx.config.vault_path)

@tool("log_activity", "Save a short timestamped note to today's journal.", {"note": {"type": "string"}}, ["note"],
          side_effects=True)
async def log_activity(ctx, a):
    from ..memory import vault as v
    v.journal_append(ctx.config.vault_path, a.get("note", ""))
    return "logged."

@tool("what_am_i_doing",
      "What the user is doing right now: focused window, open apps, whether they are away "
      "from the keyboard, whether a call is holding the screen awake, and what is playing. "
      "Use this before assuming what they are working on, and to decide whether to interrupt.",
      {},
          parallel_safe=True)
async def what_am_i_doing(ctx, a):
    from .. import context
    return context.describe()

@tool("system_stats", "Machine health: CPU/mem/GPU/disk/battery/temps.", {},
          parallel_safe=True)
async def system_stats(ctx, a):
    from ..integrations import system_stats as s
    return s.report()

@tool("capture_screen", "Look at the user's screen. 'question' = what to look for.",
      {"question": {"type": "string"}},
          parallel_safe=True)
async def capture_screen(ctx, a):
    from ..vision import analyze, screenshot
    path = screenshot.capture()
    if not path:
        return "Couldn't capture the screen."
    ans = analyze.describe(path, a.get("question", ""), ctx.config)
    if ans is None:
        return ("Screenshot taken, but no vision model is set up. Add GEMINI_API_KEY to .env, or "
                "install Ollama and `ollama pull moondream`.")
    return ans

@tool("analyze_image", "Describe/answer about an image file. path + optional question.",
      {"path": {"type": "string"}, "question": {"type": "string"}}, ["path"],
          parallel_safe=True)
async def analyze_image(ctx, a):
    from ..vision import analyze
    ans = analyze.describe(a.get("path", ""), a.get("question", ""), ctx.config)
    return ans or "No vision model set up (GEMINI_API_KEY or Ollama + moondream)."

@tool("screen_share_start", "Start watching the user's screen live (a fresh look each turn).", {},
          side_effects=True)
async def screen_share_start(ctx, a):
    from ..vision import live
    live.set_active(True)
    return "Live screen-share on — I'll keep an eye on your screen."

@tool("screen_share_stop", "Stop watching the screen live.", {},
          side_effects=True)
async def screen_share_stop(ctx, a):
    from ..vision import live
    live.set_active(False)
    return "Live screen-share off."

@tool("read_project", "Find the project open in VS Code and list its files so you can analyze it.", {},
          parallel_safe=True)
async def read_project(ctx, a):
    from ..integrations import coding
    folder = coding.active_folder()
    if not folder:
        return "I don't see a VS Code project open."
    return coding.overview(folder)

@tool(
    "code_with_antigravity",
    "Refine the user's coding prompt and delegate the task to the Antigravity CLI (agy) to write code and verify tests. Executes in the background on the active VS Code file/workspace, announcing when finished.",
    {"task": {"type": "string"}, "folder": {"type": "string"}}, ["task"],
          side_effects=True)
async def code_with_antigravity(ctx, a):
    from pathlib import Path
    from ..integrations import coding
    ctx = coding.active_context()
    folder = a.get("folder") or ctx.get("folder")
    if not folder:
        return "No VS Code or project folder is open to work on."
    task = a.get("task", "")
    target_label = ctx.get("file") or ctx.get("project_name") or Path(folder).name
    if ctx.job_runner:
        job_id = ctx.job_runner.dispatch_antigravity(
            task,
            folder=folder,
            active_file=ctx.get("file"),
            active_file_path=ctx.get("file_path"),
        )
        return (
            f"I have refined your prompt and dispatched agy CLI ({job_id}) on {target_label}. "
            "It is running autonomously in the background and I will announce when finished."
        )
    return "No task runner available."

@tool(
    "check_coding_tasks",
    "Check status and results of background coding tasks running in Antigravity.",
    {},
          parallel_safe=True)
async def check_coding_tasks(ctx, a):
    if not ctx.job_runner:
        return "No task runner available."
    return ctx.job_runner.status_report()

@tool("catch_up", "Sweep unread email + recent WhatsApp + today's calendar.", {},
          side_effects=True)
async def catch_up(ctx, a):
    from ..integrations import whatsapp
    from ..integrations.google import calendar as gcal, gmail
    out = []
    for label, fn in (("EMAIL", lambda: gmail.check(ctx.config, "is:unread")),
                      ("CALENDAR", lambda: gcal.agenda(ctx.config, 1))):
        try:
            v = fn()
            if v:
                out.append(f"{label}:\n{v}")
        except Exception:  # noqa: BLE001
            pass
    try:
        msgs = whatsapp.inbox()
        if msgs:
            out.append("WHATSAPP:\n" + "\n".join(f"{m.get('name')}: {m.get('text')}" for m in msgs[-8:]))
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(out) or "Nothing to catch up on."
