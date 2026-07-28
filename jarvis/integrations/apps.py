"""Launching and opening things — on the laptop and (best-effort) on the phone.

Laptop: open URLs in the user's browser (Opera by default) and launch desktop apps.
Phone: KDE Connect can open a URL, ring the phone, or run a preconfigured command — Android does
not allow launching arbitrary apps remotely, so that's the ceiling.
"""

from __future__ import annotations

import os
import shutil
import subprocess

_SNAP_ENV = ("LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR")


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _SNAP_ENV}


def _spawn(argv: list[str]) -> None:
    subprocess.Popen(
        argv, env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


# --- laptop ---------------------------------------------------------------
def open_url(url: str, browser: str = "opera") -> str | None:
    """Open a URL in `browser` (default Opera). Returns the resolved URL, or None if it can't."""
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    exe = shutil.which(browser) or shutil.which("opera") or shutil.which("xdg-open")
    if not exe:
        return None
    _spawn([exe, url])
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
    """'ready' | 'unauthorized' | 'none' | 'no-adb' — whether a phone is reachable over ADB."""
    if not shutil.which("adb"):
        return "no-adb"
    try:
        out = subprocess.run(["adb", "devices"], env=_env(), timeout=6, capture_output=True, text=True).stdout
    except Exception:  # noqa: BLE001
        return "none"
    rows = [r for r in out.splitlines()[1:] if r.strip()]
    if any(r.endswith("\tdevice") for r in rows):
        return "ready"
    if any("unauthorized" in r for r in rows):
        return "unauthorized"
    return "none"


def phone_mirror() -> tuple[bool, str]:
    """Mirror the phone via scrcpy. Returns (ok, message) — honest about connection problems."""
    if not shutil.which("scrcpy"):
        return False, "scrcpy isn't installed (sudo apt install scrcpy)."
    st = adb_status()
    if st == "unauthorized":
        return False, "Your phone is showing a 'Allow USB debugging?' prompt — tap Allow, then try again."
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
