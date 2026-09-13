"""Pointer, keyboard and on-screen control.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool

@tool("mouse_move", "Move the mouse cursor to screen pixel coordinates x, y.", {"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"],
          side_effects=True)
async def mouse_move(ctx, a):
    from ..integrations import desktop_control as dc
    return "moved." if dc.move(as_int(a.get("x", 0)), as_int(a.get("y", 0))) else "control not ready."

@tool("mouse_click", "Click mouse button at optional x, y coordinates.", {"button": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"}, "double": {"type": "boolean"}},
          side_effects=True)
async def mouse_click(ctx, a):
    from ..integrations import desktop_control as dc
    button = a.get("button", "left") or "left"
    double = as_bool(a.get("double", False))
    x, y = as_int(a.get("x", -1), -1), as_int(a.get("y", -1), -1)
    ok = dc.move_click(x, y, button, double) if x >= 0 and y >= 0 else dc.click(button, double)
    return f"{'double-' if double else ''}clicked {button}." if ok else "click failed."

@tool("type_text", "Type text at the keyboard focus.", {"text": {"type": "string"}}, ["text"],
          side_effects=True)
async def type_text(ctx, a):
    from ..integrations import desktop_control as dc
    return "typed." if dc.type_text(a.get("text", "")) else "control not ready."

@tool("press_keys", "Press a key combo, e.g. 'ctrl+c', 'enter'.", {"keys": {"type": "string"}}, ["keys"],
          side_effects=True)
async def press_keys(ctx, a):
    from ..integrations import desktop_control as dc
    return "pressed." if dc.press_keys(a.get("keys", "")) else "control not ready."

@tool("scroll_page", "Scroll screen up or down.", {"direction": {"type": "string"}, "amount": {"type": "integer"}},
          side_effects=True)
async def scroll_page(ctx, a):
    from ..integrations import desktop_control as dc
    return "scrolled." if dc.scroll(a.get("direction", "down") or "down", as_int(a.get("amount", 5), 5)) else "scroll failed."

@tool("find_and_click", "Find a button, icon, or text element on screen via vision and click it directly.", {"target": {"type": "string"}, "button": {"type": "string"}, "double": {"type": "boolean"}}, ["target"],
          side_effects=True)
async def find_and_click(ctx, a):
    from ..integrations import desktop_control as dc
    return dc.find_and_click(a.get("target", ""), button=a.get("button", "left") or "left", double=as_bool(a.get("double", False)), config=ctx.config)
