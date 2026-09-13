"""The tool registry: declarations live with the tool, and nothing can drift from them."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.tools import base
from jarvis.tools.base import Registry, Tool, ToolContext, as_bool, as_int, dispatch, tool


@pytest.fixture
def reg():
    return Registry()


@pytest.fixture
def ctx():
    return ToolContext(config=SimpleNamespace(), job_runner=None)


def test_schema_shape(reg):
    @tool("demo", "A demo tool.", {"x": {"type": "string"}}, ["x"], registry=reg)
    async def demo(ctx, a):
        return "ok"

    fn = reg.get("demo").schema()["function"]
    assert fn["name"] == "demo"
    assert fn["description"] == "A demo tool."
    assert fn["parameters"]["required"] == ["x"]


def test_a_tool_with_no_parameters_still_gets_an_object(reg):
    # Some OpenAI-compatible servers reject an empty properties object.
    @tool("nullary", "Takes nothing.", registry=reg)
    async def nullary(ctx, a):
        return "ok"

    props = reg.get("nullary").schema()["function"]["parameters"]["properties"]
    assert props and "_dummy" in props


def test_duplicate_names_are_rejected(reg):
    @tool("dup", "First.", registry=reg)
    async def one(ctx, a):
        return "1"

    with pytest.raises(ValueError, match="duplicate"):
        @tool("dup", "Second.", registry=reg)
        async def two(ctx, a):
            return "2"


def test_dispatch_runs_the_tool(reg, ctx):
    import asyncio

    @tool("echo", "Echoes.", {"text": {"type": "string"}}, registry=reg)
    async def echo(ctx, a):
        return a.get("text", "")

    assert asyncio.run(dispatch(reg, ctx, "echo", {"text": "hi"})) == "hi"


def test_dispatch_handles_sync_tools(reg, ctx):
    import asyncio

    @tool("sync", "Not a coroutine.", registry=reg)
    def sync_tool(ctx, a):
        return 42

    assert asyncio.run(dispatch(reg, ctx, "sync", {})) == "42"


def test_dispatch_reports_an_unknown_tool(reg, ctx):
    import asyncio

    assert "unknown tool" in asyncio.run(dispatch(reg, ctx, "nope", {}))


def test_a_raising_tool_becomes_text_not_an_exception(reg, ctx):
    import asyncio

    @tool("boom", "Explodes.", registry=reg)
    async def boom(ctx, a):
        raise RuntimeError("kaboom")

    out = asyncio.run(dispatch(reg, ctx, "boom", {}))
    assert "tool error (boom)" in out and "kaboom" in out


def test_confirm_is_false_when_nobody_can_be_asked():
    import asyncio

    ctx = ToolContext(config=SimpleNamespace())
    assert asyncio.run(ctx.confirm("do something irreversible")) is False


def test_confirm_delegates_when_a_handler_exists():
    import asyncio

    seen = []

    async def yes(desc):
        seen.append(desc)
        return True

    ctx = ToolContext(config=SimpleNamespace(), confirm_fn=yes)
    assert asyncio.run(ctx.confirm("send an email")) is True
    assert seen == ["send an email"]


def test_requires_gates_a_tool_out_of_the_menu(reg):
    @tool("always", "No requirement.", registry=reg)
    async def always(ctx, a):
        return ""

    @tool("gated", "Needs google.", requires="google", registry=reg)
    async def gated(ctx, a):
        return ""

    assert {t.name for t in reg.available()} == {"always"}
    assert {t.name for t in reg.available({"google"})} == {"always", "gated"}


def test_flags_are_read_off_the_definitions(reg):
    @tool("readonly", "Reads.", parallel_safe=True, registry=reg)
    async def readonly(ctx, a):
        return ""

    @tool("writes", "Writes.", side_effects=True, registry=reg)
    async def writes(ctx, a):
        return ""

    assert reg.parallel_safe() == {"readonly"}
    assert reg.side_effects() == {"writes"}


def test_coercions():
    assert as_int("7") == 7 and as_int("7.9") == 7
    assert as_int(None, 3) == 3 and as_int("abc", 3) == 3
    assert as_bool("yes") and as_bool("TRUE") and as_bool("1")
    assert not as_bool("no") and not as_bool(None)


# --- the real registry ------------------------------------------------------------------------
def test_every_shipped_tool_declares_a_description():
    base.load_domains()
    missing = [t.name for t in base.REGISTRY.all() if not t.description.strip()]
    assert missing == [], f"tools with no description: {missing}"


def test_no_tool_is_both_parallel_safe_and_side_effecting():
    base.load_domains()
    both = base.REGISTRY.parallel_safe() & base.REGISTRY.side_effects()
    assert both == frozenset(), f"contradictory declarations: {sorted(both)}"


def test_google_tools_all_declare_their_requirement():
    base.load_domains()
    ungated = [t.name for t in base.REGISTRY.all()
               if t.name.startswith("google_") and t.requires != "google"]
    assert ungated == [], f"google tools missing requires=: {ungated}"


def test_the_agent_and_the_audit_read_the_same_declarations():
    from jarvis.agent.groq_tools import parallel_safe_tools, side_effect_tools

    base.load_domains()
    assert parallel_safe_tools() == base.REGISTRY.parallel_safe()
    assert side_effect_tools() == base.REGISTRY.side_effects()


def test_every_import_inside_every_tool_actually_resolves():
    """Tool bodies import lazily, so a wrong module path only fails when the tool is called.

    That is how the domain split broke `omnicore`: the bodies were written in `jarvis/agent/`,
    where `from . import omnicore` meant `jarvis.agent.omnicore`, and after the move a single dot
    pointed the module at itself. Nothing failed until the tool ran. Resolve them all up front.
    """
    import ast
    import importlib
    import pathlib

    failures = []
    for path in sorted(pathlib.Path("jarvis/tools").glob("*.py")):
        if path.name in ("base.py", "__init__.py"):
            continue
        # A relative import is anchored on the module's PARENT package.
        package = "jarvis.tools"
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = "." * node.level + (node.module or "")
            try:
                mod = importlib.import_module(target, package=package)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{path.name}:{node.lineno} cannot import {target}: {exc}")
                continue
            for alias in node.names:
                if not hasattr(mod, alias.name):
                    try:
                        # `from .. import context` -> "..context", not "...context".
                        sep = "" if target.endswith(".") else "."
                        importlib.import_module(f"{target}{sep}{alias.name}", package=package)
                    except Exception:  # noqa: BLE001
                        failures.append(
                            f"{path.name}:{node.lineno} {target} has no {alias.name!r}")
    assert failures == [], "\n".join(failures)
