"""Tool definitions that live next to their metadata, in files you can open.

`build_registry` was one 831-line function holding every tool in a single closure, with an
834-line near-duplicate in `sdk_tools.py`. Three things followed from that shape:

  * No tool could be tested on its own. Getting at one meant constructing the whole registry.
  * Adding an ability meant editing a 900-line file, so the file only ever grew.
  * Facts *about* a tool — is it safe to run concurrently, does it have side effects — could not
    be written next to the tool. They ended up as hand-maintained sets in `groq_core.py` and
    `scripts/audit.py`, and had already drifted apart: two names that were no longer tools, two
    tools in neither list.

Here a tool is a decorated function in a domain module, and everything true about it is stated at
the definition:

    @tool("set_volume", "Set output volume percent (0-150).",
          {"percent": {"type": "string"}}, ["percent"], side_effects=True)
    async def set_volume(ctx, a):
        ...

`parallel_safe` and `side_effects` are then read off the registry rather than remembered in two
other files. The agent loop asks the registry which calls it may batch; the audit asks it which
tools it must not invoke. Neither can drift, because there is only one statement of the fact.
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

ToolFn = Callable[["ToolContext", dict], Any]


@dataclass
class ToolContext:
    """Everything a tool is allowed to reach for. Passed in rather than closed over."""

    config: Any
    job_runner: Any = None
    confirm_fn: Optional[Callable[[str], Awaitable[bool]]] = None

    async def confirm(self, description: str) -> bool:
        """Ask the user to approve something irreversible. False when nobody can be asked."""
        if self.confirm_fn is None:
            return False
        return bool(await self.confirm_fn(description))


@dataclass
class Tool:
    name: str
    description: str
    params: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    fn: Optional[ToolFn] = None

    #: Safe to run concurrently with other parallel_safe tools in the same batch. Read-only, no
    #: shared mutable state, no user-visible effect. The agent loop batches these.
    parallel_safe: bool = False

    #: Sends a message, changes a setting, moves the pointer, spends money or opens a window.
    #: The audit refuses to invoke these, and reports them as skipped rather than passed.
    side_effects: bool = False

    #: Name of an optional subsystem this tool needs (e.g. "google"). Tools whose requirement is
    #: unavailable are left out of the menu entirely rather than failing when called.
    requires: str = ""

    def schema(self) -> dict:
        props = dict(self.params)
        if not props:
            # An empty object upsets some OpenAI-compatible servers; give them a field to ignore.
            props = {"_dummy": {"type": "string", "description": "Ignore this field, leave empty."}}
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": props, "required": self.required},
            },
        }


class Registry:
    """The set of known tools. Module-level by default; construct your own in tests."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def available(self, have: Iterable[str] = ()) -> list[Tool]:
        """Tools whose `requires` is satisfied. `have` names the subsystems that are usable."""
        ready = set(have)
        return [t for t in self._tools.values() if not t.requires or t.requires in ready]

    def parallel_safe(self) -> frozenset[str]:
        return frozenset(t.name for t in self._tools.values() if t.parallel_safe)

    def side_effects(self) -> frozenset[str]:
        return frozenset(t.name for t in self._tools.values() if t.side_effects)


REGISTRY = Registry()


def tool(name: str, description: str, params: Optional[dict] = None,
         required: Optional[list[str]] = None, *, parallel_safe: bool = False,
         side_effects: bool = False, requires: str = "", registry: Optional[Registry] = None):
    """Register a tool. The decorated function takes (ctx, args) and returns text."""

    def deco(fn: ToolFn) -> ToolFn:
        target = REGISTRY if registry is None else registry
        target.add(Tool(
            name=name, description=description, params=params or {}, required=required or [],
            fn=fn, parallel_safe=parallel_safe, side_effects=side_effects, requires=requires,
        ))
        return fn

    return deco


def load_domains(package: str = "jarvis.tools") -> Registry:
    """Import every domain module so its tools register themselves.

    Discovery rather than a hand-maintained import list: a new domain is a new file, which is the
    whole point of the split.
    """
    pkg = importlib.import_module(package)
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        importlib.import_module(f"{package}.{info.name}")
    return REGISTRY


async def dispatch(registry: Registry, ctx: ToolContext, name: str, args: dict) -> str:
    """Run one tool by name, turning any failure into text the model can read."""
    entry = registry.get(name)
    if entry is None or entry.fn is None:
        return f"unknown tool: {name}"
    try:
        result = entry.fn(ctx, args or {})
        if asyncio.iscoroutine(result):
            result = await result
        return str(result)
    except Exception as exc:  # noqa: BLE001 - a broken tool must not end the turn
        return f"tool error ({name}): {exc}"


# --- small shared coercions -------------------------------------------------------------------
# Models send numbers as strings, booleans as "yes", and omit optional fields entirely.
def as_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")
