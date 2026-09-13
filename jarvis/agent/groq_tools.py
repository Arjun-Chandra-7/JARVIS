"""Adapter from the tool registry to the OpenAI function-calling shape the brains expect.

This module used to *be* the tools: one `build_registry` function, 831 lines, every tool defined
in a single closure. The definitions now live in `jarvis/tools/<domain>.py`, one file per area,
each tool declaring its own concurrency and side-effect facts (see `jarvis/tools/base.py` for why).

What is left here is the adapter: build a `ToolContext`, ask the registry which tools are usable on
this machine, and hand back `(schemas, dispatch)` exactly as before. Callers did not change.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from ..config import Config
from ..tools.base import REGISTRY, ToolContext, dispatch as _dispatch, load_domains


def available_subsystems(config: Config) -> set[str]:
    """Optional subsystems that are actually usable, gating tools that declare `requires`.

    Google is the only one so far. Offering `google_email_send` on a machine that has never
    authorised is worse than not offering it: the model tries, fails, and spends a turn saying so.
    An earlier version of this check looked for a `credentials.json` in the vault — a path Jarvis
    has never written — which silently hid all eight Google tools on an authorised machine.
    """
    have: set[str] = set()
    if config.google_client_secret.exists() or config.google_token_file.exists():
        have.add("google")
    return have


def build_registry(config: Config, job_runner, confirm_fn: Optional[Callable[[str], Awaitable[bool]]]):
    """Return (schemas, dispatch). `dispatch(name, args)` runs a tool and returns text."""
    load_domains()
    ctx = ToolContext(config=config, job_runner=job_runner, confirm_fn=confirm_fn)
    tools = REGISTRY.available(available_subsystems(config))
    schemas = [t.schema() for t in tools]

    async def dispatch(name: str, args: dict) -> str:
        return await _dispatch(REGISTRY, ctx, name, args)

    return schemas, dispatch


def parallel_safe_tools() -> frozenset[str]:
    """Tools the agent loop may run concurrently, read off the definitions rather than a list."""
    load_domains()
    return REGISTRY.parallel_safe()


def side_effect_tools() -> frozenset[str]:
    """Tools an audit must not invoke, read off the definitions rather than a list."""
    load_domains()
    return REGISTRY.side_effects()
