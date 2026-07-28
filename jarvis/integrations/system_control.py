"""Local machine control: volume, media playback, brightness, lock/suspend, connectivity, DND.

Everything here shells out to tools already on the box (PipeWire's wpctl, gdbus for MPRIS media and
GNOME brightness, loginctl/systemctl, nmcli, bluetoothctl, gsettings) — no extra installs. The
subprocess env is sanitized so a snap-confined launcher's leaked LD_LIBRARY_PATH can't crash them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

_SNAP = ("LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR")
_SINK = "@DEFAULT_AUDIO_SINK@"


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _SNAP}


def _run(argv: list[str], timeout: int = 8) -> subprocess.CompletedProcess | None:
    if not shutil.which(argv[0]):
        return None
    try:
        return subprocess.run(argv, env=_env(), timeout=timeout, capture_output=True, text=True)
    except Exception:  # noqa: BLE001
        return None


# --- volume (PipeWire) ----------------------------------------------------
def set_volume(percent: int) -> bool:
    pct = max(0, min(150, int(percent)))
    return _run(["wpctl", "set-volume", _SINK, f"{pct / 100:.2f}"]) is not None


def adjust_volume(delta: int) -> bool:
    sign = "+" if delta >= 0 else "-"
    return _run(["wpctl", "set-volume", _SINK, f"{abs(delta) / 100:.2f}{sign}"]) is not None


def mute_audio(mute: bool) -> bool:
    return _run(["wpctl", "set-mute", _SINK, "1" if mute else "0"]) is not None


def get_volume() -> str | None:
    r = _run(["wpctl", "get-volume", _SINK])
    if not r or r.returncode != 0:
        return None
    m = re.search(r"([\d.]+)", r.stdout)
    if not m:
        return None
    return f"{round(float(m.group(1)) * 100)}%" + (" (muted)" if "MUTED" in r.stdout else "")


# --- media (MPRIS via gdbus) ---------------------------------------------
def _mpris_players() -> list[str]:
    r = _run([
        "gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
        "--object-path", "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.ListNames",
    ])
    if not r or r.returncode != 0:
        return []
    return sorted(set(re.findall(r"org\.mpris\.MediaPlayer2\.[\w.\-]+", r.stdout)))


def media_control(action: str) -> str | None:
    """action: play_pause | next | previous | stop. Returns the player controlled, or None."""
    method = {"play_pause": "PlayPause", "next": "Next", "previous": "Previous", "stop": "Stop"}.get(
        action.lower().replace("-", "_")
    )
    if not method:
        return None
    players = _mpris_players()
    if not players:
        return None
    player = players[0]
    ok = _run([
        "gdbus", "call", "--session", "--dest", player,
        "--object-path", "/org/mpris/MediaPlayer2",
        "--method", f"org.mpris.MediaPlayer2.Player.{method}",
    ])
    return player.split(".")[-1] if ok and ok.returncode == 0 else None


# --- brightness (GNOME SettingsDaemon) -----------------------------------
def set_brightness(percent: int) -> bool:
    pct = max(1, min(100, int(percent)))
    r = _run([
        "gdbus", "call", "--session", "--dest", "org.gnome.SettingsDaemon.Power",
        "--object-path", "/org/gnome/SettingsDaemon/Power",
        "--method", "org.freedesktop.DBus.Properties.Set",
        "org.gnome.SettingsDaemon.Power.Screen", "Brightness", f"<int32 {pct}>",
    ])
    return bool(r and r.returncode == 0)


# --- power / session ------------------------------------------------------
def lock_screen() -> bool:
    return _run(["loginctl", "lock-session"]) is not None


def suspend() -> bool:
    return _run(["systemctl", "suspend"]) is not None


# --- connectivity ---------------------------------------------------------
def set_radio(radio: str, on: bool) -> bool:
    r = radio.lower()
    if "blue" in r or r == "bt":
        return _run(["bluetoothctl", "power", "on" if on else "off"]) is not None
    if "wifi" in r or "wi-fi" in r or r == "wlan":
        return _run(["nmcli", "radio", "wifi", "on" if on else "off"]) is not None
    return False


# --- do not disturb (GNOME) ----------------------------------------------
def do_not_disturb(on: bool) -> bool:
    # show-banners false = notifications suppressed
    return _run([
        "gsettings", "set", "org.gnome.desktop.notifications", "show-banners",
        "false" if on else "true",
    ]) is not None
