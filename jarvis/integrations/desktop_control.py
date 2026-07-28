"""Desktop input control — move the mouse, click, type, and press key combos.

On Wayland the only reliable path is ydotool (kernel-level input via /dev/uinput), so this wraps it;
on X11 it falls back to xdotool. Requires one-time setup (scripts/enable-control.sh): the ydotoold
daemon running and the user in the `input` group.

Coordinates are REAL screen pixels. Screenshots Jarvis reads are downscaled, so it must scale image
coordinates up to screen coordinates — see screenshot.scale_note().
"""

from __future__ import annotations

import os
import shutil
import subprocess

_SNAP = ("LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR")

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


def move(x: int, y: int) -> bool:
    tool = available()
    if tool == "ydotool":
        return _run(["ydotool", "mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y))])
    if tool == "xdotool":
        return _run(["xdotool", "mousemove", str(int(x)), str(int(y))])
    return False


_YCLICK = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}
_XBTN = {"left": "1", "middle": "2", "right": "3"}


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
    return move(x, y) and click(button, double)


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
