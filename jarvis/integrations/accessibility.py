"""Native UI labels and bounds from the active application's AT-SPI tree."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

_BRIDGE = Path(__file__).resolve().parents[2] / "scripts" / "atspi_snapshot.py"
_ACTIVATOR = Path(__file__).resolve().parents[2] / "scripts" / "atspi_activate.py"
_COMMON = {"the", "a", "an", "my", "on", "screen", "playlist", "button", "link", "item", "icon"}


def _bridge_env() -> dict:
    env = os.environ.copy()
    runtime = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    env.setdefault("DISPLAY", ":0")
    return env


def activate(node: dict, app_hint: str = "") -> dict:
    """Perform a control's own accessibility action — the press a screen reader would make.

    Wayland never tells a client where its window is, so AT-SPI reports every control at (0, 0)
    and a coordinate click cannot be aimed. Measured on this machine: all sixty-two controls in
    gnome-calculator came back starting [0, 0, ...]. Acting through the control itself needs no
    coordinates, cannot land on the wrong thing, and does not move a pointer the user is using.
    """
    payload = json.dumps({"app": app_hint, "path": node.get("path") or [],
                          "expect": str(node.get("name", "")), "action": ""})
    try:
        result = subprocess.run(["/usr/bin/python3", str(_ACTIVATOR), payload],
                                capture_output=True, text=True, timeout=15, env=_bridge_env())
        data = json.loads(result.stdout)
        return data if isinstance(data, dict) else {"ok": False, "reason": "No reply."}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"Accessibility action failed ({type(exc).__name__})."}


_GSETTING = ("org.gnome.desktop.interface", "toolkit-accessibility")


def enabled() -> bool:
    """Whether toolkits are publishing their control trees at all."""
    try:
        out = subprocess.run(["gsettings", "get", *_GSETTING],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_enabled() -> bool:
    """Turn the accessibility bus on if it is off.

    With this false — the default on this machine — every application publishes its window frame
    and not one control inside it, so there is nothing to click by name. It is the single switch
    that decides whether the desktop is addressable. Applications already running keep their old
    behaviour until restarted, which is why this is worth doing early rather than on demand.
    """
    if enabled():
        return True
    try:
        subprocess.run(["gsettings", "set", *_GSETTING, "true"], timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return enabled()


def snapshot(app_hint: str = "") -> dict:
    """Return {ok, app, window, nodes}; fail softly when accessibility is unavailable."""
    try:
        argv = ["/usr/bin/python3", str(_BRIDGE)]
        if app_hint:
            argv.append(app_hint)
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=12, env=_bridge_env())
        data = json.loads(result.stdout)
        if isinstance(data, dict) and data.get("app") == "mutter-x11-frames":
            return {"ok": False, "reason": "Only the window frame is accessible; use screen vision",
                    "nodes": [], "window": data.get("window", "")}
        # One node, and it is the window itself: the toolkit is not publishing its controls.
        nodes = data.get("nodes") if isinstance(data, dict) else None
        if nodes is not None and len(nodes) <= 1 and not enabled():
            ensure_enabled()
            return {"ok": False, "nodes": [],
                    "reason": ("The desktop was not publishing its controls. I have turned "
                               "accessibility on — reopen that window and try again.")}
        return data if isinstance(data, dict) else {"ok": False, "nodes": []}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"ok": False, "reason": "Accessibility service unavailable", "nodes": []}


def _words(value: str) -> list[str]:
    return [w for w in re.findall(r"[\w']+", value.casefold()) if w not in _COMMON]


# Actions that mean "use this control", as opposed to the clipboard and menu actions that GTK
# hangs off every piece of text. A label reading "7" and the button reading "7" are both in the
# tree; only one of them is the thing to press.
_REAL_ACTIONS = ("click", "dodefault", "activate", "press", "jump", "open", "toggle")
_INTERACTIVE = ("button", "toggle button", "check box", "radio button", "menu item", "link",
                "list item", "tab", "combo box", "entry", "text", "slider", "page tab")


def _is_actionable(node: dict) -> bool:
    return any(a.casefold() in _REAL_ACTIONS for a in (node.get("actions") or []))


def identity(node: dict) -> tuple:
    """What makes two matches genuinely different controls.

    The old check compared screen centres. Under Wayland every control reports (0, 0), so two
    different buttons of the same size looked like the same place and the ambiguity guard passed
    silently — it would pick one and click it. The path in the tree is the real identity.
    """
    path = node.get("path")
    if isinstance(path, list) and path:
        return ("path", tuple(path))
    rect = node.get("rect") or [0, 0, 0, 0]
    if len(rect) == 4 and any(rect):
        return ("center", rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
    return ("name", str(node.get("name", "")), str(node.get("role", "")))


def choose(nodes: list[dict], target: str) -> tuple[dict | None, str]:
    """Find one visible control matching the name; refuse when two different ones match."""
    wanted = _words(target)
    exact = (target or "").strip().casefold()

    # Word tokens are built from [\w']+, which finds nothing at all in "×", "=", "→" or "✓" — so
    # every symbol control on screen was invisible to the matcher and fell through to vision.
    # A whole-name comparison catches those, and is the strongest kind of match in any case.
    if exact:
        literal = [n for n in nodes
                   if str(n.get("name", "")).strip().casefold() == exact]
        if literal:
            usable = [n for n in literal if _is_actionable(n)] or literal
            if len({identity(n) for n in usable}) == 1:
                return usable[0], ""
            return None, f"Several visible controls match '{target}'; I won't guess which to click."

    if not wanted:
        return None, f"'{target}' isn't in the active app's accessible controls."
    candidates: list[tuple[int, dict]] = []
    for node in nodes:
        label = _words(str(node.get("name", "")))
        if not label:
            continue
        rect = node.get("rect")
        if isinstance(rect, list) and len(rect) == 4 and any(rect):
            x, y, w, h = rect
            # Only judge a rectangle that is actually reported; zeros mean "Wayland won't say".
            if (x or y or w or h) and (w < 4 or h < 4 or x < 0 or y < 0):
                continue
        if label == wanted:
            score = 100
        elif all(w in label for w in wanted):
            score = 75 - 5 * (len(label) - len(wanted))
        elif len(wanted) > 1 and all(w in wanted for w in label):
            score = 60
        else:
            continue
        # A control you can actually use beats a label that merely says the same word.
        if _is_actionable(node):
            score += 12
        if str(node.get("role", "")).casefold() in _INTERACTIVE:
            score += 6
        candidates.append((score, node))
    if not candidates:
        return None, f"'{target}' isn't in the active app's accessible controls."
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    top = candidates[0][0]
    best = [n for score, n in candidates if score >= top - 5]
    if len({identity(n) for n in best}) > 1:
        return None, f"Several visible controls match '{target}'; I won't guess which to click."
    return best[0], ""


def readable(data: dict, limit: int = 80) -> str:
    if not data.get("ok"):
        return data.get("reason", "Native accessibility is unavailable.")
    labels = []
    seen = set()
    for node in data.get("nodes", []):
        name = str(node.get("name", "")).strip()
        if name and name not in seen:
            seen.add(name)
            labels.append(f"{name} ({node.get('role', 'control')})")
        if len(labels) >= limit:
            break
    return (f"Active app: {data.get('app', '')} — {data.get('window', '')}. "
            + ("Visible controls: " + "; ".join(labels) if labels else "No named visible controls."))
