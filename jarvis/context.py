"""What the user is doing right now, from signals this desktop will actually give up.

An assistant that cannot tell whether you are heads-down in an editor, in a call, or away from the
keyboard has to ask before it can be useful — and asking is the thing that makes proactivity
annoying. Jarvis had the pieces (an idle-time probe buried in the voice session, a media check in
the Spotify endpoint) but no single answer to "what is happening".

GNOME on Wayland deliberately hides most of this. There is no portable focused-window API, the
Shell's own D-Bus interface is not exposed without an extension, and `xdotool getactivewindow`
returns nothing. What does work, verified on this machine:

    wmctrl -lx                      the X11/XWayland window list with WM_CLASS — which covers
                                    VS Code, Chrome, Opera and every Electron app, i.e. most of
                                    what this user actually works in
    xprop -root _NET_ACTIVE_WINDOW  the focused window, when it is one of those
    Mutter IdleMonitor              milliseconds since the last input, over D-Bus
    playerctl                       what is playing and where
    SessionManager.IsInhibited(8)   something is holding off idle — a call, a video, a screen share

Native Wayland-only windows are invisible to the first two. That is a real limit, not a bug to be
worked around, so `windows()` reports what it can see and the caller is told the list is partial.
Every probe is short, cached and non-fatal.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from typing import Optional

_CACHE: dict[str, tuple[float, object]] = {}
_LOCK = threading.Lock()
_TTL = 2.0          # a couple of seconds is plenty; these are polled by a HUD, not a loop


def _clean_env() -> dict:
    """Strip the snap/conda library pollution that breaks system binaries launched from here."""
    return {k: v for k, v in os.environ.items() if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")}


def _run(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_clean_env())
        return r.stdout.strip()
    except Exception:  # noqa: BLE001 - a missing binary is a normal state here
        return ""


def _cached(key: str, fn, ttl: float = _TTL):
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _LOCK:
        _CACHE[key] = (now, value)
    return value


# --------------------------------------------------------------------------- windows
def _parse_wmctrl(out: str) -> list[dict]:
    windows = []
    for line in out.splitlines():
        # 0x01c00004  0 code.code   host  Title with spaces
        m = re.match(r"^(0x[0-9a-f]+)\s+(-?\d+)\s+(\S+)\s+(\S+)\s+(.*)$", line)
        if not m:
            continue
        wid, desktop, wmclass, _host, title = m.groups()
        app = wmclass.split(".")[-1] or wmclass
        windows.append({"id": wid, "desktop": int(desktop), "app": app, "title": title.strip()})
    return windows


def windows() -> list[dict]:
    """Open windows visible to X11. Native Wayland windows will not appear here."""
    return _cached("windows", lambda: _parse_wmctrl(_run(["wmctrl", "-lx"])))


def active_window() -> Optional[dict]:
    """The focused window, when it is an X11/XWayland one."""
    def probe():
        out = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        m = re.search(r"(0x[0-9a-f]+)", out)
        if not m:
            return None
        # wmctrl zero-pads ids; xprop does not, so compare numerically.
        want = int(m.group(1), 16)
        for w in windows():
            if int(w["id"], 16) == want:
                return w
        return None
    return _cached("active", probe)


# --------------------------------------------------------------------------- attention
def idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard or pointer input, via GNOME's Mutter IdleMonitor."""
    def probe():
        out = _run([
            "gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
            "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
            "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime",
        ])
        m = re.search(r"(\d+)", out)
        return round(int(m.group(1)) / 1000.0, 1) if m else None
    return _cached("idle", probe, ttl=1.0)


def idle_inhibited() -> bool:
    """True when something is deliberately keeping the session awake — a call, a video, a share."""
    def probe():
        out = _run([
            "gdbus", "call", "--session", "--dest", "org.gnome.SessionManager",
            "--object-path", "/org/gnome/SessionManager",
            "--method", "org.gnome.SessionManager.IsInhibited", "8",
        ])
        return "true" in out.lower()
    return bool(_cached("inhibited", probe, ttl=5.0))


def media() -> Optional[dict]:
    """What is playing, if anything."""
    def probe():
        players = [p for p in _run(["playerctl", "-l"]).splitlines() if p.strip()]
        if not players:
            return None
        status = _run(["playerctl", "status"])
        if status.lower() not in ("playing", "paused"):
            return None
        meta = _run(["playerctl", "metadata", "--format",
                     "{{playerName}}\t{{artist}}\t{{title}}"])
        parts = meta.split("\t")
        while len(parts) < 3:
            parts.append("")
        # A registered player with no track is noise — KDE Connect always registers one for the
        # paired phone whether or not anything is playing on it.
        if not parts[2].strip():
            return None
        return {"player": parts[0], "artist": parts[1], "title": parts[2],
                "playing": status.lower() == "playing"}
    return _cached("media", probe, ttl=4.0)


# --------------------------------------------------------------------------- summary
#  WM_CLASS is stable where a window title is not, so activity is inferred from the class.
_ACTIVITY = [
    ({"code", "codium", "cursor", "sublime_text", "jetbrains-idea", "nvim", "gnome-terminal-server",
      "kitty", "alacritty", "konsole"}, "coding"),
    ({"chrome", "google-chrome", "firefox", "opera", "brave-browser", "chromium"}, "browsing"),
    ({"slack", "discord", "telegram-desktop", "signal", "thunderbird", "geary"}, "messaging"),
    ({"zoom", "teams-for-linux", "skype"}, "in a call"),
    ({"spotify", "vlc", "mpv", "totem"}, "watching or listening"),
    ({"libreoffice", "obsidian", "zotero", "evince", "okular"}, "reading or writing"),
]


def _activity_for(app: str) -> Optional[str]:
    low = (app or "").lower()
    for names, label in _ACTIVITY:
        if low in names:
            return label
    return None


def snapshot() -> dict:
    """One dict describing the user's current situation. Safe to call often."""
    wins = windows()
    active = active_window()
    idle = idle_seconds()
    inhibited = idle_inhibited()
    now_playing = media()

    apps: list[str] = []
    for w in wins:
        if w["app"] not in apps:
            apps.append(w["app"])

    activity = _activity_for(active["app"]) if active else None
    if activity is None and apps:
        for app in apps:
            activity = _activity_for(app)
            if activity:
                break

    away = idle is not None and idle > 300
    return {
        "active": active,
        "windows": wins,
        "apps": apps,
        "activity": activity,
        "idle_s": idle,
        "away": away,
        "idle_inhibited": inhibited,
        "media": now_playing,
        # X11 only: a fully Wayland-native app will be missing from `windows`.
        "partial": True,
    }


def describe() -> str:
    """The snapshot as one sentence, for the brain or the HUD."""
    s = snapshot()
    bits = []
    if s["away"]:
        mins = int((s["idle_s"] or 0) // 60)
        bits.append(f"Away from the keyboard for about {mins} minute{'s' if mins != 1 else ''}")
    else:
        bits.append("At the keyboard")

    if s["active"]:
        bits.append(f'focused on "{s["active"]["title"]}" ({s["active"]["app"]})')
    elif s["apps"]:
        bits.append("with " + ", ".join(s["apps"][:4]) + " open")

    if s["activity"]:
        bits.append(f"— looks like {s['activity']}")
    if s["idle_inhibited"]:
        bits.append("· something is holding the screen awake (a call or a video)")
    if s["media"] and s["media"]["playing"]:
        m = s["media"]
        bits.append(f"· playing {m['title']}" + (f" by {m['artist']}" if m["artist"] else ""))
    if not s["windows"]:
        bits.append("(no X11 windows visible — native Wayland apps are not listed)")
    return " ".join(bits) + "."


def is_interruptible() -> tuple[bool, str]:
    """Whether now is a reasonable moment to speak up unprompted, and why.

    Deliberately conservative: the cost of interrupting a call is much higher than the cost of
    staying quiet for another few minutes.
    """
    s = snapshot()
    if s["idle_inhibited"]:
        return False, "something is holding the screen awake — probably a call or a video"
    if s["activity"] == "in a call":
        return False, "a call app is in the foreground"
    if s["away"]:
        return False, "away from the keyboard"
    return True, "at the keyboard and nothing is holding the session"
