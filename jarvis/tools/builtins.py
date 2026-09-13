"""Shell, files and the web — the primitives every other tool is built on.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..agent.permissions import is_destructive
from .base import as_bool, as_int, tool

@tool("run_bash", "Run a shell command on this Linux machine and return stdout/stderr.",
      {"command": {"type": "string"}}, ["command"],
          side_effects=True)
async def run_bash(ctx, a):
    cmd = a.get("command", "")
    if is_destructive(cmd) and not ctx.config.allow_unconfirmed_shell:
        if not await ctx.confirm(f"run this command:\n    {cmd}"):
            return "User declined the command."
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        return out.strip()[:6000] or f"(exit {r.returncode}, no output)"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"

@tool("read_file", "Read a text file.", {"path": {"type": "string"}}, ["path"],
          parallel_safe=True)
async def read_file(ctx, a):
    try:
        return Path(a["path"]).expanduser().read_text(errors="ignore")[:8000]
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"

@tool("write_file", "Create or overwrite a text file.",
      {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"],
          side_effects=True)
async def write_file(ctx, a):
    try:
        p = Path(a["path"]).expanduser().resolve()
        if p.exists() and not ctx.config.allow_unconfirmed_shell:
            head = p.read_text(errors="ignore")[:400]
            if "author: jarvis" not in head and "/scratch" not in str(p) and "/tmp" not in str(p):
                if not await ctx.confirm(f"overwrite existing file not created by Jarvis:\n    {p}"):
                    return "User declined overwriting this file."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(a.get("content", ""))
        return f"wrote {p}"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"

@tool("list_dir", "List a directory.", {"path": {"type": "string"}}, ["path"],
          parallel_safe=True)
async def list_dir(ctx, a):
    try:
        return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in Path(a["path"]).expanduser().iterdir()))[:4000]
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"

@tool("web_search", "Search the web (returns a short summary + top links).", {"query": {"type": "string"}}, ["query"],
          parallel_safe=True)
async def web_search(ctx, a):
    import httpx
    q = a.get("query", "")
    try:
        r = httpx.get("https://api.duckduckgo.com/", params={"q": q, "format": "json", "no_html": 1}, timeout=8)
        j = r.json()
        bits = [j.get("AbstractText", "")]
        for t in (j.get("RelatedTopics") or [])[:5]:
            if isinstance(t, dict) and t.get("Text"):
                bits.append("- " + t["Text"])
        out = "\n".join(b for b in bits if b)
        return out[:3000] or "No instant answer; try web_fetch on a specific URL."
    except Exception as exc:  # noqa: BLE001
        return f"search error: {exc}"

@tool("web_fetch", "Fetch a URL and return its text.", {"url": {"type": "string"}}, ["url"],
          parallel_safe=True)
async def web_fetch(ctx, a):
    import re
    import httpx
    try:
        html = httpx.get(a["url"], timeout=10, follow_redirects=True).text
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html))
        return re.sub(r"\s+", " ", text).strip()[:5000]
    except Exception as exc:  # noqa: BLE001
        return f"fetch error: {exc}"

@tool("deep_research",
      "Do serious, up-to-date research using the user's Perplexity account (browses the live web, "
      "synthesises sources). Use this WHENEVER the user asks to 'research', wants depth/current "
      "information, or is thinking through a project — not for trivial facts (use web_search for those).",
      {"query": {"type": "string"}}, ["query"],
          side_effects=True)
async def deep_research(ctx, a):
    from ..integrations import research
    q = a.get("query", "")
    try:
        r = await research.research(q)
    except Exception as exc:  # noqa: BLE001
        return f"Deep research failed ({exc}). Is Playwright/Chrome set up? Try `--perplexity-login`."
    if not r.get("ok"):
        return r.get("text", "No result.")
    out = r["text"]
    if r.get("sources"):
        out += "\n\nSources:\n" + "\n".join("- " + s for s in r["sources"])
    return out[:6000]
