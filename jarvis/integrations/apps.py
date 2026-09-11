"""Launching and opening things — on the laptop and (best-effort) on the phone.

Laptop: open URLs in the user's browser (Opera by default) and launch desktop apps.
Phone: KDE Connect can open a URL, ring the phone, or run a preconfigured command — Android does
not allow launching arbitrary apps remotely, so that's the ceiling.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_SNAP_ENV = ("LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR")


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _SNAP_ENV}


def _spawn(argv: list[str]) -> bool:
    try:
        process = subprocess.Popen(
            argv,
            env=_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    try:
        return process.wait(timeout=0.4) == 0
    except subprocess.TimeoutExpired:
        return True


# --- laptop ---------------------------------------------------------------
def open_url(url: str, browser: str = "opera", *, new_window: bool = False) -> str | None:
    """Open a URL in `browser` and report whether the launch was accepted."""
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    exe = shutil.which(browser) or shutil.which("opera") or shutil.which("xdg-open")
    if not exe:
        return None
    argv = [exe]
    if new_window and Path(exe).name in {"opera", "opera-stable"}:
        argv.append("--new-window")
    argv.append(url)
    if not _spawn(argv):
        # Opera may return nonzero after handing a URL to its existing window.
        # Its live process is enough evidence that the launch was accepted.
        if Path(exe).name not in {"opera", "opera-stable"}:
            return None
        try:
            running = subprocess.run(
                ["pgrep", "-af", "opera"],
                env=_env(),
                timeout=2,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
        except OSError:
            running = False
        if not running:
            return None
    if new_window and shutil.which("wmctrl"):
        _spawn(["wmctrl", "-a", "Opera"])
    return url


def launch_app(name: str) -> str | None:
    """Launch a desktop app by binary name or .desktop id. Returns a description, or None."""
    exe = shutil.which(name)
    if exe:
        _spawn([exe])
        return f"launched {name}"
    if shutil.which("gtk-launch"):
        desktop_id = name[:-8] if name.endswith(".desktop") else name
        try:
            subprocess.run(
                ["gtk-launch", desktop_id], env=_env(), timeout=8,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            )
            return f"launched {name}"
        except Exception:  # noqa: BLE001
            pass
    return None


# --- phone (KDE Connect) --------------------------------------------------
def _kc(device_id: str | None, *args: str) -> bool:
    if not shutil.which("kdeconnect-cli"):
        return False
    argv = ["kdeconnect-cli"]
    if device_id:
        argv += ["-d", device_id]
    argv += list(args)
    try:
        r = subprocess.run(argv, env=_env(), timeout=10, capture_output=True)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def phone_open_url(url: str, device_id: str | None = None) -> bool:
    """Open a URL on the phone (launches the phone's default handler, e.g. YouTube/browser)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return _kc(device_id, "--share", url)


def phone_ring(device_id: str | None = None) -> bool:
    return _kc(device_id, "--ring")


def adb_status() -> str:
    """Return the ADB connection state without attempting to change the phone.

    Values are ``ready``, ``unauthorized``, ``offline``, ``none``, ``server-error`` and
    ``no-adb``.  Keeping a daemon-start failure distinct from "no phone" makes the
    mirror error actionable (and avoids falsely asking the user to reconnect a phone).
    """
    if not shutil.which("adb"):
        return "no-adb"
    try:
        result = subprocess.run(
            ["adb", "devices"], env=_env(), timeout=6, capture_output=True, text=True
        )
    except Exception:  # noqa: BLE001
        return "server-error"
    if result.returncode != 0:
        return "server-error"
    out = result.stdout
    rows = [r for r in out.splitlines()[1:] if r.strip()]
    if any(r.endswith("\tdevice") for r in rows):
        return "ready"
    if any("unauthorized" in r for r in rows):
        return "unauthorized"
    if any("\toffline" in r for r in rows):
        return "offline"
    return "none"


def phone_mirror() -> tuple[bool, str]:
    """Launch a controllable scrcpy mirror after validating the ADB transport.

    This does not issue any command to the phone until ``scrcpy`` starts.  It is safe
    to call as a connectivity check; scrcpy itself opens the normal mirror window.
    """
    if not shutil.which("scrcpy"):
        return False, "scrcpy isn't installed (sudo apt install scrcpy)."
    st = adb_status()
    if st == "unauthorized":
        return False, "Your phone is showing a 'Allow USB debugging?' prompt — tap Allow, then try again."
    if st == "offline":
        return False, "Your phone is connected but ADB reports it offline. Reconnect the USB cable, unlock it, then try again."
    if st == "server-error":
        return False, "ADB could not start or connect to its local server. Run `adb kill-server && adb start-server`, then try again."
    if st != "ready":
        return False, ("No phone detected over USB. Plug it in, turn on USB debugging in Developer "
                       "options, and set the USB mode to File Transfer.")
    try:
        _spawn(["scrcpy", "--window-title=JARVIS Phone", "--always-on-top", "--stay-awake"])
        return True, "Phone is on screen."
    except Exception:  # noqa: BLE001
        return False, "Couldn't start the phone mirror."


def phone_call(number: str, device_id: str | None = None) -> bool:
    """Best-effort: open the phone's dialer for `number` (a tel: link). The user taps to dial —
    Android doesn't allow a remote to place a call outright."""
    digits = "".join(c for c in number if c.isdigit() or c == "+")
    if not digits:
        return False
    return _kc(device_id, "--share", f"tel:{digits}")


# --- clipboard ------------------------------------------------------------
def read_clipboard() -> str | None:
    """Return the current clipboard text (Wayland wl-paste, else X11 xclip/xsel), or None."""
    for argv in (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "-b"],
    ):
        if not shutil.which(argv[0]):
            continue
        try:
            r = subprocess.run(argv, env=_env(), timeout=5, capture_output=True)
            if r.returncode == 0:
                return r.stdout.decode(errors="ignore")
        except Exception:  # noqa: BLE001
            continue
    return None
