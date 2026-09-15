"""Desktop input control — move the mouse, click, type, and press key combos.

On Wayland precise absolute pointer input uses the RemoteDesktop portal and
libei. ydotool remains available for keyboard and relative mouse gestures;
on X11 xdotool handles both.

Coordinates are REAL screen pixels. Screenshots Jarvis reads are downscaled, so it must scale image
coordinates up to screen coordinates — see screenshot.scale_note().
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import time
import select
import sys
from pathlib import Path

_SNAP = ("LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR")
_PORTAL_BRIDGE = Path(__file__).resolve().parents[2] / "scripts" / "portal_input.py"
_portal_process = None
_portal_regions = []

# Linux input-event-codes for the keys we support by friendly name.
_KEYS = {
    "ctrl": 29, "control": 29, "rctrl": 97,
    "shift": 42, "rshift": 54, "alt": 56, "altgr": 100,
    "super": 125, "meta": 125, "win": 125, "cmd": 125,
    "enter": 28, "return": 28, "esc": 1, "escape": 1, "tab": 15, "space": 57,
    "backspace": 14, "delete": 111, "del": 111, "insert": 110,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "home": 102, "end": 107, "pageup": 104, "pgup": 104, "pagedown": 109, "pgdn": 109,
    "capslock": 58, "printscreen": 99, "menu": 127,
    "minus": 12, "equal": 12, "comma": 51, "dot": 52, "period": 52, "slash": 53, "semicolon": 39,
}
# exact letter codes (Linux input-event-codes)
_LETTERS = {"a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35, "i": 23,
            "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25, "q": 16, "r": 19,
            "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45, "y": 21, "z": 44}
_KEYS.update(_LETTERS)
_KEYS.update({str(d): (2 + i) for i, d in enumerate("1234567890")})  # 1->2 ... 0->11
_KEYS.update({f"f{n}": (58 + n) for n in range(1, 11)})  # f1..f10 = 59..68
_KEYS.update({"f11": 87, "f12": 88})


def _env() -> dict:
    e = {k: v for k, v in os.environ.items() if k not in _SNAP}
    e.setdefault("YDOTOOL_SOCKET", f"{os.environ.get('XDG_RUNTIME_DIR', '/run/user/1000')}/.ydotool_socket")
    return e


def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _run(argv: list[str]) -> bool:
    try:
        r = subprocess.run(argv, env=_env(), timeout=10, capture_output=True, text=True)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def available() -> str | None:
    """Return 'ydotool', 'xdotool', or None (with a hint) depending on what's usable."""
    if _wayland() and shutil.which("ydotool"):
        sock = _env()["YDOTOOL_SOCKET"]
        return "ydotool" if os.path.exists(sock) else None
    if shutil.which("xdotool"):
        return "xdotool"
    return None


def _portal_request(command: dict) -> bool:
    """Send one pointer action to a persistent libei session, failing closed."""
    global _portal_process, _portal_regions
    if not _wayland() or not _PORTAL_BRIDGE.exists():
        return False
    try:
        if _portal_process is None or _portal_process.poll() is not None:
            env = _env()
            runtime = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
            env.setdefault("DISPLAY", ":0")
            # System Python has PyGObject; python-libei is pure Python in the venv.
            env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if "site-packages" in p)
            _portal_process = subprocess.Popen(
                ["/usr/bin/python3", str(_PORTAL_BRIDGE)], env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
            )
            if not select.select([_portal_process.stdout], [], [], 45)[0]:
                _portal_process.kill()
                _portal_process = None
                return False
            ready = json.loads(_portal_process.stdout.readline())
            if not ready.get("ok"):
                _portal_process = None
                return False
            _portal_regions = ready.get("regions", [])
        if _portal_process.stdin is None or _portal_process.stdout is None:
            return False
        _portal_process.stdin.write(json.dumps(command) + "\n")
        _portal_process.stdin.flush()
        if not select.select([_portal_process.stdout], [], [], 5)[0]:
            _portal_process.kill()
            _portal_process = None
            return False
        return bool(json.loads(_portal_process.stdout.readline()).get("ok"))
    except (OSError, ValueError, BrokenPipeError, TypeError):
        _portal_process = None
        return False


def _portal_point(x: int, y: int) -> tuple[int, int] | None:
    """Map capture pixels into the portal's logical desktop pixel region."""
    from ..vision import screenshot
    if len(_portal_regions) != 1:
        # Multi-monitor regions need a monitor-by-monitor mapping; never guess.
        return None
    region = _portal_regions[0]
    real = getattr(screenshot, "_last_geom", {}).get("real")
    if not real or not all(real):
        return None
    rx, ry = region["position"]
    width, height = region["size"]
    return rx + round(x * width / real[0]), ry + round(y * height / real[1])


def move(x: int, y: int) -> bool:
    tool = available()
    if tool == "ydotool":
        if _portal_request({"op": "ready"}):
            point = _portal_point(int(x), int(y))
            if point and _portal_request({"op": "move", "x": point[0], "y": point[1]}):
                return True
        # ydotool's --absolute is relative motion from a corner and is
        # distorted by pointer acceleration. Never claim precise placement.
        return False
    if tool == "xdotool":
        return _run(["xdotool", "mousemove", str(int(x)), str(int(y))])
    return False


def move_rel(dx: int, dy: int) -> bool:
    """Relative pointer move — for a phone trackpad. Bounded so one gesture can't fling the cursor."""
    dx, dy = max(-400, min(400, int(dx))), max(-400, min(400, int(dy)))
    tool = available()
    if tool == "ydotool":
        return _run(["ydotool", "mousemove", "-x", str(dx), "-y", str(dy)])
    if tool == "xdotool":
        return _run(["xdotool", "mousemove_relative", "--", str(dx), str(dy)])
    return False


_YCLICK = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}
_XBTN = {"left": "1", "middle": "2", "right": "3"}
_YBTN = {"left": 0, "right": 1, "middle": 2}


def click(button: str = "left", double: bool = False) -> bool:
    tool = available()
    b = button.lower()
    if tool == "ydotool":
        code = _YCLICK.get(b, "0xC0")
        ok = _run(["ydotool", "click", code])
        if double:
            ok = _run(["ydotool", "click", code]) and ok
        return ok
    if tool == "xdotool":
        argv = ["xdotool", "click"]
        if double:
            argv += ["--repeat", "2"]
        return _run(argv + [_XBTN.get(b, "1")])
    return False


def move_click(x: int, y: int, button: str = "left", double: bool = False) -> bool:
    if available() == "ydotool":
        # The bridge negotiates the session before its regions are known.
        if _portal_process is None or _portal_process.poll() is not None:
            if not _portal_request({"op": "ready"}):
                return False
        point = _portal_point(int(x), int(y))
        return bool(point and _portal_request({"op": "move_click", "x": point[0], "y": point[1],
                                               "button": button, "double": double}))
    return move(x, y) and click(button, double)


def _button_event(button: str, down: bool, tool: str) -> bool:
    button = button.lower()
    if button not in _XBTN:
        return False
    if tool == "ydotool":
        code = (0x40 if down else 0x80) | _YBTN[button]
        return _run(["ydotool", "click", hex(code)])
    if tool == "xdotool":
        return _run(["xdotool", "mousedown" if down else "mouseup", _XBTN[button]])
    return False


def hold_mouse(button: str = "left", duration_ms: int = 500) -> bool:
    """Hold a mouse button briefly, then always release it (e.g. charging a game shot)."""
    tool = available()
    duration_ms = max(50, min(5000, int(duration_ms)))
    if not tool or button.lower() not in _XBTN:
        return False
    pressed = _button_event(button, True, tool)
    try:
        if pressed:
            time.sleep(duration_ms / 1000)
    finally:
        released = _button_event(button, False, tool)
    return pressed and released


def drag(start_x: int, start_y: int, end_x: int, end_y: int,
         button: str = "left", duration_ms: int = 500) -> bool:
    """Drag through intermediate positions; release the button even if movement fails."""
    tool = available()
    duration_ms = max(100, min(5000, int(duration_ms)))
    if not tool or button.lower() not in _XBTN or not all(
        0 <= int(v) <= 32768 for v in (start_x, start_y, end_x, end_y)
    ) or not move(start_x, start_y):
        return False
    pressed = _button_event(button, True, tool)
    moved = True
    steps = max(2, min(60, duration_ms // 25))
    try:
        if pressed:
            for i in range(1, steps + 1):
                x = round(start_x + (end_x - start_x) * i / steps)
                y = round(start_y + (end_y - start_y) * i / steps)
                moved = move(x, y) and moved
                if not moved:
                    break
                time.sleep(duration_ms / steps / 1000)
    finally:
        released = _button_event(button, False, tool)
    return pressed and moved and released


def hold_keys(combo: str, duration_ms: int = 500) -> bool:
    """Hold a key or combo for a bounded duration, then release in reverse order."""
    tool = available()
    names = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    duration_ms = max(50, min(5000, int(duration_ms)))
    if not tool or not names:
        return False
    if tool == "ydotool":
        codes = [_KEYS.get(n) for n in names]
        if any(c is None for c in codes):
            return False
        down = [f"{c}:1" for c in codes]
        up = [f"{c}:0" for c in reversed(codes)]
        pressed = _run(["ydotool", "key", *down])
        try:
            if pressed:
                time.sleep(duration_ms / 1000)
        finally:
            released = _run(["ydotool", "key", *up])
        return pressed and released
    pressed: list[str] = []
    worked = False
    released = True
    try:
        for name in names:
            if not _run(["xdotool", "keydown", name]):
                break
            pressed.append(name)
        else:
            time.sleep(duration_ms / 1000)
            worked = True
    finally:
        for name in reversed(pressed):
            released = _run(["xdotool", "keyup", name]) and released
    return worked and released


def type_text(text: str) -> bool:
    tool = available()
    if tool == "ydotool":
        return _run(["ydotool", "type", "--", text])
    if tool == "xdotool":
        return _run(["xdotool", "type", "--", text])
    return False


def press_keys(combo: str) -> bool:
    """combo like 'ctrl+c', 'alt+Tab', 'super', 'ctrl+shift+t'."""
    tool = available()
    names = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if tool == "xdotool":  # xdotool takes friendly names directly
        return _run(["xdotool", "key", "+".join(names)])
    if tool == "ydotool":
        codes = [_KEYS.get(n) for n in names]
        if any(c is None for c in codes):
            return False
        seq = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
        return _run(["ydotool", "key", *seq])
    return False


def scroll(direction: str = "down", amount: int = 5) -> bool:
    """Scroll up or down on the screen."""
    tool = available()
    d = direction.lower()
    if tool == "xdotool":
        btn = "4" if "up" in d else "5"
        return _run(["xdotool", "click", "--repeat", str(max(1, amount)), btn])
    if tool == "ydotool":
        key = "pageup" if "up" in d else "pagedown"
        code = _KEYS.get(key, 109)
        ok = True
        for _ in range(max(1, amount // 2)):
            ok = _run(["ydotool", "key", f"{code}:1", f"{code}:0"]) and ok
        return ok
    return False


def _ground_point(ans: str | None, format_name: str, target: str,
                  img: tuple[int, int], real: tuple[int, int] | None) -> tuple[int, int] | str:
    """Convert a single grounded target to real pixels, or explain why it is unsafe."""
    if not ans or "vision error" in str(ans).lower():
        return f"Could not analyze screenshot to locate '{target}': {ans or 'no vision provider'}"
    try:
        data = json.loads(str(ans).strip().removeprefix("```json").removesuffix("```").strip())
        if format_name == "bbox_1000":
            if isinstance(data, list) and len(data) == 4 and all(
                isinstance(v, (int, float)) for v in data
            ):
                box, label = data, ""
            elif isinstance(data, dict) and "bbox_2d" in data:
                box, label = data["bbox_2d"], str(data.get("label", "")).casefold()
            elif isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
                box, label = data[0]["bbox_2d"], str(data[0].get("label", "")).casefold()
            else:
                return f"I couldn't find one unambiguous '{target}' on screen, so I didn't act."
            if len(box) != 4:
                raise ValueError("bad box")
            x1, y1, x2, y2 = [float(v) for v in box]
            if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
                raise ValueError("box outside normalized image")
            terms = [t for t in target.casefold().split() if len(t) > 2 and t not in {"the", "button", "icon", "here"}]
            if label and terms and not any(t in label for t in terms):
                return f"Vision labeled a different object than '{target}', so I didn't act."
            x_val = round((x1 + x2) / 2000 * img[0])
            y_val = round((y1 + y2) / 2000 * img[1])
        else:
            if not isinstance(data, dict) or data.get("found") is False:
                return f"I couldn't find an unambiguous '{target}' on screen."
            x_val, y_val = int(data["x"]), int(data["y"])
            confidence = float(data["confidence"])
            if confidence < 0.65:
                return f"I can only place '{target}' with {confidence:.0%} confidence, so I didn't act."
    except (ValueError, TypeError, KeyError, IndexError):
        return f"Could not reliably pinpoint '{target}' on screen. Vision reported: {str(ans)[:300]}"

    if not (0 <= x_val < img[0] and 0 <= y_val < img[1]):
        return f"Vision placed '{target}' outside the captured image, so I didn't act."
    if real and img and img[0] > 0 and img[1] > 0:
        scale_x = real[0] / img[0]
        scale_y = real[1] / img[1]
        real_x = int(x_val * scale_x)
        real_y = int(y_val * scale_y)
    else:
        real_x, real_y = x_val, y_val

    if real and not (0 <= real_x < real[0] and 0 <= real_y < real[1]):
        return f"Vision placed '{target}' outside the desktop, so I didn't act."
    return real_x, real_y


def find_and_click(target: str, button: str = "left", double: bool = False, config=None) -> str:
    """Automatically find a visual element or text on screen using vision and click it."""
    from ..vision import analyze, screenshot
    if available() is None:
        return "Desktop control isn't ready — run scripts/enable-control.sh once."
    path = screenshot.capture()
    if not path:
        return "Failed to capture screenshot for visual targeting."
    geom = getattr(screenshot, "_last_geom", {})
    real, img = geom.get("real"), geom.get("img")
    if not img:
        return "I couldn't read the screenshot dimensions, so I didn't click."
    point = _ground_point(*analyze.ground(path, target, config), target, img, real)
    if isinstance(point, str):
        return point.replace("didn't act", "didn't click")
    real_x, real_y = point
    img_x = round(real_x * img[0] / real[0]) if real else real_x
    img_y = round(real_y * img[1] / real[1]) if real else real_y
    if not analyze.verify_point(path, target, img_x, img_y, config):
        return f"I could not confirm '{target}' at the proposed screen position, so I didn't click."
    ok = move_click(real_x, real_y, button=button, double=double)
    if ok:
        return f"Located '{target}' at real screen coordinates ({real_x}, {real_y}) and clicked {button}."
    return f"Located '{target}' at ({real_x}, {real_y}), but mouse click failed to execute."


def find_and_drag(start_target: str, end_target: str, duration_ms: int = 500,
                  button: str = "left", config=None) -> str:
    """Ground both ends against one screenshot, then drag and release."""
    from ..vision import analyze, screenshot
    if available() is None:
        return "Desktop control isn't ready — run scripts/enable-control.sh once."
    path = screenshot.capture()
    if not path:
        return "Failed to capture screenshot for visual dragging."
    geom = getattr(screenshot, "_last_geom", {})
    real, img = geom.get("real"), geom.get("img")
    if not img:
        return "I couldn't read the screenshot dimensions, so I didn't drag."
    start = _ground_point(*analyze.ground(path, start_target, config), start_target, img, real)
    if isinstance(start, str):
        return start
    end = _ground_point(*analyze.ground(path, end_target, config), end_target, img, real)
    if isinstance(end, str):
        return end
    for target, point in ((start_target, start), (end_target, end)):
        img_x = round(point[0] * img[0] / real[0]) if real else point[0]
        img_y = round(point[1] * img[1] / real[1]) if real else point[1]
        if not analyze.verify_point(path, target, img_x, img_y, config):
            return f"I could not confirm '{target}' at the proposed position, so I didn't drag."
    ok = drag(*start, *end, button=button, duration_ms=duration_ms)
    if ok:
        return (f"Dragged from '{start_target}' at {start} to '{end_target}' at {end} "
                "and released. Check the screen for the result.")
    return "Both targets were located, but the mouse drag failed."


def click_target(target: str, button: str = "left", double: bool = False, config=None) -> str:
    """Use a named control: its own accessibility action first, a pointer only when it has none.

    Order matters, and it is the opposite of what it looks like it should be. Acting through the
    control is not a fallback for clicking — it is the better mechanism. It cannot miss, it works
    on a window that is partly covered, it does not move a pointer out from under the user's hand,
    and under Wayland it is the only thing that works at all: the compositor never tells a client
    where its window sits, so AT-SPI reports every control at (0, 0) and a pointer cannot be aimed
    at one. Verified here — all sixty-two controls in gnome-calculator reported [0, 0, w, h].

    A pointer is still needed for anything with no accessibility tree: games, canvases, video,
    remote desktops. Those go through find_and_click, which looks at the screen.
    """
    from . import accessibility

    tree = accessibility.snapshot()
    where = tree.get("app") or "the active app"
    if tree.get("ok") and tree.get("nodes"):
        node, reason = accessibility.choose(tree["nodes"], target)
        if node is not None:
            if accessibility._is_actionable(node):
                done = accessibility.activate(node, tree.get("app", ""))
                if done.get("ok"):
                    return (f"Used the '{node['name']}' {node.get('role') or 'control'} "
                            f"in {where}.")
                # Fall through to the pointer: a refused action may still be clickable.
                refusal = done.get("reason", "")
            else:
                refusal = ""

            rect = node.get("rect") or [0, 0, 0, 0]
            if any(rect) and available() is not None:
                x, y, w, h = rect
                center_x, center_y = x + w // 2, y + h // 2
                if move_click(center_x, center_y, button, double):
                    return (f"Clicked '{node['name']}' in {where} at "
                            f"({center_x}, {center_y}).")
            if refusal:
                return f"I found '{node['name']}' in {where} but {refusal[0].lower()}{refusal[1:]}"
        elif "Several visible controls" in reason:
            return reason

    if available() is None:
        return "Desktop control isn't ready — run scripts/enable-control.sh once."
    return find_and_click(target, button, double, config)
