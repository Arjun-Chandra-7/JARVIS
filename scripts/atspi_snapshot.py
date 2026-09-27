#!/usr/bin/python3
"""Read the active native application's AT-SPI accessibility tree as JSON.

The project venv does not include PyGObject, so Jarvis runs this with system Python. This helper
only reads UI state. It deliberately returns one active window, not every open application's data.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys


def _active_x11_title() -> str:
    try:
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        raw = subprocess.check_output(["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                                      env=env, text=True, timeout=2)
        match = re.search(r"0x[0-9a-fA-F]+", raw)
        if not match:
            return ""
        wid = int(match.group(), 16)
        for line in subprocess.check_output(["wmctrl", "-l"], env=env,
                                            text=True, timeout=2).splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4 and int(parts[0], 16) == wid:
                return parts[3].strip()
    except Exception:
        pass
    return ""


def _safe(call, default=None):
    try:
        return call()
    except Exception:
        return default


def _resolve_root(Atspi, app_hint: str = ""):
    """The window to read or act on: the hinted app, else the active one.

    Shared with atspi_activate.py so that a control addressed from a snapshot is looked up in the
    same window it was found in.
    """
    desktop = Atspi.get_desktop(0)
    x11_title = _active_x11_title().casefold()
    app_hint = app_hint.casefold().strip()
    roots = []
    for a in range(min(100, _safe(desktop.get_child_count, 0))):
        app = _safe(lambda: desktop.get_child_at_index(a))
        if app is None:
            continue
        app_name = _safe(app.get_name, "") or ""
        for w in range(min(30, _safe(app.get_child_count, 0))):
            win = _safe(lambda: app.get_child_at_index(w))
            if win is None:
                continue
            title = _safe(win.get_name, "") or ""
            states = _safe(win.get_state_set)
            active = bool(states and _safe(lambda: states.contains(Atspi.StateType.ACTIVE), False))
            title_match = bool(x11_title and title and
                               (title.casefold() in x11_title or x11_title in title.casefold()))
            hinted = bool(app_hint and (app_hint in app_name.casefold() or app_hint in title.casefold()))
            if hinted or (not app_hint and (active or title_match)):
                roots.append((win, app_name, title, active))

    if not roots:
        return None, "", ""
    roots.sort(key=lambda item: item[3], reverse=True)
    root, app_name, title, _ = roots[0]
    return root, app_name, title


# States a ScreenElement reports. Read, not inferred: "checked" is what the toolkit says.
_STATES = ("focused", "focusable", "editable", "enabled", "sensitive", "checked", "selected",
           "expanded", "pressed", "active", "visible", "showing", "multi_line")
_SECRET_ROLES = {"password text"}


def describe(Atspi, node) -> dict:
    """What a caller may know about one node: states, and its value unless it is a secret."""
    states = _safe(node.get_state_set)
    names = []
    if states is not None:
        for state in _STATES:
            kind = getattr(Atspi.StateType, state.upper(), None)
            if kind is not None and _safe(lambda k=kind: states.contains(k), False):
                names.append(state)
    role = _safe(node.get_role_name, "") or ""
    value = ""
    if role not in _SECRET_ROLES:
        value_iface = _safe(node.get_value_iface)
        if value_iface is not None:
            current = _safe(value_iface.get_current_value)
            if current is not None:
                value = str(current)
        if not value and "editable" in names:
            text_iface = _safe(node.get_text_iface)
            if text_iface is not None:
                count = _safe(text_iface.get_character_count, 0) or 0
                value = (_safe(lambda: text_iface.get_text(0, min(count, 500)), "") or "")
    return {"states": names, "value": value, "secret": role in _SECRET_ROLES}


def snapshot(app_hint: str = "") -> dict:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    Atspi.init()
    root, app_name, title = _resolve_root(Atspi, app_hint)
    if root is None:
        return {"ok": False, "reason": "No active accessible window", "nodes": []}
    nodes = []
    stack = [(root, 0, ())]
    seen = 0
    while stack and seen < 1600 and len(nodes) < 450:
        node, depth, path = stack.pop()
        seen += 1
        states = _safe(node.get_state_set)
        showing = bool(states is None or _safe(
            lambda: states.contains(Atspi.StateType.SHOWING), False))
        name = _safe(node.get_name, "") or ""
        role = _safe(node.get_role_name, "") or ""
        if not name and role not in _SECRET_ROLES:
            text_iface = _safe(node.get_text_iface)
            if text_iface is not None:
                count = _safe(text_iface.get_character_count, 0) or 0
                if count:
                    name = (_safe(lambda: text_iface.get_text(0, min(count, 120)), "") or "").strip()
        rect = None
        component = _safe(node.get_component_iface)
        if component is not None:
            box = _safe(lambda: component.get_extents(Atspi.CoordType.SCREEN))
            if box is not None and box.width > 0 and box.height > 0:
                rect = [int(box.x), int(box.y), int(box.width), int(box.height)]
        action_iface = _safe(node.get_action_iface)
        actions = []
        if action_iface is not None:
            count = min(8, _safe(action_iface.get_n_actions, 0) or 0)
            actions = [(_safe(lambda i=i: action_iface.get_action_name(i), "") or "")
                       for i in range(count)]
        # Under Wayland a client is not told where its window sits, so AT-SPI reports every
        # control at x=0,y=0 and a coordinate click is impossible. The index chain from the window
        # root addresses a control without any coordinates at all, and the accessibility action is
        # both more reliable and more precise than aiming a pointer at it.
        extra = describe(Atspi, node)
        # Nameless text fields are exactly what dictation and "type into the search box" need,
        # so an editable or focused node is kept even without a label.
        keep = name or "editable" in extra["states"] or "focused" in extra["states"]
        if showing and keep and len(name) <= 240:
            nodes.append({"name": name, "role": role, "rect": rect or [0, 0, 0, 0],
                          "actions": actions, "path": list(path), **extra})
        if depth < 18:
            count = min(250, _safe(node.get_child_count, 0) or 0)
            for i in reversed(range(count)):
                child = _safe(lambda i=i: node.get_child_at_index(i))
                if child is not None:
                    stack.append((child, depth + 1, path + (i,)))
    return {"ok": True, "app": app_name, "window": title, "nodes": nodes,
            "truncated": bool(stack)}


if __name__ == "__main__":
    try:
        print(json.dumps(snapshot(sys.argv[1] if len(sys.argv) > 1 else ""), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": type(exc).__name__, "nodes": []}))
        sys.exit(1)
