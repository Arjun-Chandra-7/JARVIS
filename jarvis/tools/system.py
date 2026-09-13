"""Volume, brightness, media, power and launching things.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool

@tool("set_volume", "Set output volume percent (0-150).", {"percent": {"type": "string"}}, ["percent"],
          side_effects=True)
async def set_volume(ctx, a):
    from ..integrations import system_control as sc
    sc.set_volume(as_int(a.get("percent"), 50))
    return "done."

@tool("media_control", "Media: play_pause/next/previous/stop.", {"action": {"type": "string"}}, ["action"],
          side_effects=True)
async def media_control(ctx, a):
    from ..integrations import system_control as sc
    return sc.media_control(a.get("action", "")) or "nothing playing."

@tool("set_brightness", "Set screen brightness percent (1-100).", {"percent": {"type": "string"}}, ["percent"],
          side_effects=True)
async def set_brightness(ctx, a):
    from ..integrations import system_control as sc
    sc.set_brightness(as_int(a.get("percent"), 70))
    return "done."

@tool("lock_screen", "Lock the screen.", {},
          side_effects=True)
async def lock_screen(ctx, a):
    from ..integrations import system_control as sc
    sc.lock_screen()
    return "locking."

@tool("do_not_disturb", "Silence notifications. on = 'true' or 'false'.", {"on": {"type": "string"}}, ["on"],
          side_effects=True)
async def dnd(ctx, a):
    from ..integrations import system_control as sc
    from ..preferences import set_notifications
    set_notifications(not as_bool(a.get("on", True)))
    sc.do_not_disturb(as_bool(a.get("on", True)))
    return "done."

@tool("open_url", "Open a URL in the browser (Opera).", {"url": {"type": "string"}}, ["url"],
          side_effects=True)
async def open_url(ctx, a):
    from ..integrations import apps
    opened = apps.open_url(a.get("url", ""))
    return f"Opened {opened}." if opened else "I couldn't open a browser window."

@tool("launch_app", "Launch a desktop app by name.", {"name": {"type": "string"}}, ["name"],
          side_effects=True)
async def launch_app(ctx, a):
    from ..integrations import apps
    return apps.launch_app(a.get("name", "")) or "not found."

@tool("read_clipboard", "Read the clipboard text.", {},
          parallel_safe=True)
async def read_clipboard(ctx, a):
    from ..integrations import apps
    return apps.read_clipboard() or "(clipboard empty/unavailable)"
