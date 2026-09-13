"""Full-laptop autonomy switches.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

import subprocess

from ..agent.permissions import is_destructive
from .base import as_bool, as_int, tool

@tool("enable_full_laptop_autonomy", "Unlock unconfirmed shell execution and full computer automation permissions so Jarvis can control all tools and the whole laptop fully.",
      {"enable": {"type": "boolean"}},
          side_effects=True)
async def enable_full_laptop_autonomy(ctx, a):
    val = as_bool(a.get("enable", True))
    ctx.config.allow_unconfirmed_shell = val
    from ..agent import omnicore
    omnicore.record_event("System Autonomy", "Jarvis", f"Full laptop autonomy set to: {val}", config=ctx.config)
    return f"Full laptop autonomy is now {'ENABLED (Unrestricted machine mastery)' if val else 'DISABLED (Standard safety confirmation gate active)'}."

@tool("control_laptop_full", "Execute advanced system, GUI, or machine operations to manage any application, tool, window, or hardware parameter on the laptop.",
      {"command_or_script": {"type": "string"}, "explanation": {"type": "string"}}, ["command_or_script"],
          side_effects=True)
async def control_laptop_full(ctx, a):
    cmd = a.get("command_or_script", "")
    from ..agent import omnicore
    if is_destructive(cmd) and not ctx.config.allow_unconfirmed_shell:
        if not await ctx.confirm(f"run this system command:\n    {cmd}"):
            return "User declined the command."
    omnicore.record_event("Laptop Control", "Jarvis", f"Executing: {cmd} ({a.get('explanation','')})", config=ctx.config)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        return f"[Omni-Control Execution Result] Return Code {r.returncode}:\n{out.strip()[:6000] or '(Command executed successfully, no terminal output)'}"
    except Exception as exc:
        return f"[Omni-Control Error]: {exc}"
