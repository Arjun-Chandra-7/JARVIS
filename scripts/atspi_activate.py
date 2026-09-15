#!/usr/bin/python3
"""Activate one control in the accessibility tree, addressed by its path.

Why not just click its coordinates: under Wayland a client is never told where its own window
sits on screen, so AT-SPI reports every control at x=0,y=0 (verified on this machine — sixty-two
controls in gnome-calculator, every rectangle starting [0, 0, …]). Aiming a pointer is therefore
impossible for native windows. Performing the control's own accessibility action needs no
coordinates, cannot miss, moves nothing the user is holding, and is what a screen reader does.

The path is the chain of child indices from the window root, as reported by atspi_snapshot.py.
The expected name is checked before acting: a tree can change between the snapshot and the click,
and pressing whatever now happens to sit at that index would be worse than refusing.

Run with system Python — the project venv has no PyGObject.
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from atspi_snapshot import _safe, _resolve_root  # noqa: E402


def activate(app_hint: str, path: list[int], expect: str, action: str = "") -> dict:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    Atspi.init()
    root, app_name, title = _resolve_root(Atspi, app_hint)
    if root is None:
        return {"ok": False, "reason": "No active accessible window."}

    node = root
    for index in path:
        child = _safe(lambda i=index: node.get_child_at_index(i))
        if child is None:
            return {"ok": False, "reason": "That control is no longer where it was."}
        node = child

    name = (_safe(node.get_name, "") or "").strip()
    if expect and name.casefold() != expect.casefold():
        return {"ok": False,
                "reason": f"That position now holds '{name}', not '{expect}' — not touching it."}

    iface = _safe(node.get_action_iface)
    if iface is None:
        return {"ok": False, "reason": f"'{name}' exposes no action to perform."}

    count = _safe(iface.get_n_actions, 0) or 0
    names = [(_safe(lambda i=i: iface.get_action_name(i), "") or "") for i in range(count)]
    wanted = action or ""
    index = None
    if wanted:
        index = next((i for i, n in enumerate(names) if n == wanted), None)
        if index is None:
            return {"ok": False, "reason": f"'{name}' has no '{wanted}' action ({names})."}
    else:
        # What a screen reader would do: the control's primary action, whatever it calls it.
        for preferred in ("click", "doDefault", "activate", "press", "jump", "open"):
            index = next((i for i, n in enumerate(names) if n == preferred), None)
            if index is not None:
                break
        if index is None and names:
            index = 0
    if index is None:
        return {"ok": False, "reason": f"'{name}' has no actions."}

    done = bool(_safe(lambda: iface.do_action(index), False))
    return {"ok": done, "name": name, "role": _safe(node.get_role_name, "") or "",
            "action": names[index], "app": app_name, "window": title,
            "reason": "" if done else f"The '{names[index]}' action on '{name}' was refused."}


if __name__ == "__main__":
    try:
        payload = json.loads(sys.argv[1])
        print(json.dumps(activate(payload.get("app", ""), payload.get("path", []),
                                  payload.get("expect", ""), payload.get("action", ""))))
    except Exception as exc:  # noqa: BLE001 - the caller only ever sees JSON
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
