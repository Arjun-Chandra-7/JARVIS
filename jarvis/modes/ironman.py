"""«Jarvis, Iron Man mode» — the desktop becomes a workshop.

The sequence, in order, because the order is most of the effect:

    1. the overview opens, so the screen visibly gathers itself
    2. everything that is not the work closes
    3. what remains is laid out: the editor on the left, ChatGPT and a terminal stacked right
    4. the overlay turns gold
    5. a short sting plays and Jarvis says it is armed

It stays until you say to come out of it. Nothing here is destructive: windows are asked to close
the way clicking the X asks them to, so anything with unsaved work says so and stays.

Two deliberate exceptions to "close everything". Spotify survives, because the overlay shows what
is playing and a mode that kills the music to show you the music is silly. And the browser
survives, because ChatGPT lives in it and is part of the layout.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from ..integrations import desktop_control as input_

# Kept through the sweep. Everything else on screen is asked to go.
KEEP = ("code", "visual studio code", "jarvis", "spotify", "opera", "chrome", "chromium",
        "firefox", "terminal", "konsole", "alacritty", "kitty")

CHATGPT_URL = "https://chatgpt.com/"

# A short rising sting, synthesised rather than sampled — a few seconds of the real thing would be
# somebody's recording, and this needs no permission from anyone.
STING_S = 2.6


@dataclass
class State:
    started: float
    closed: tuple


_on: Optional[State] = None


def on() -> bool:
    return _on is not None


def state() -> Optional[State]:
    return _on


# --------------------------------------------------------------------------- what was said
_START = re.compile(r"(?i)\b(?:iron\s*man|ironman)\s*mode\b")
_STOP = re.compile(r"(?i)\b(?:back\s+to\s+)?normal\s*mode\b|\b(?:exit|leave|end|stop|quit)\s+"
                   r"(?:the\s+)?(?:iron\s*man|ironman)\s*mode\b")
_ASKING = re.compile(r"(?ix)^\s*(?:what|what'?s|how|why|when|is|are|does|can|tell\s+me)\b")


def asked_to_start(text: str) -> bool:
    said = (text or "").strip()
    if _ASKING.match(said) or asked_to_stop(said):
        return False
    return bool(_START.search(said))


def asked_to_stop(text: str) -> bool:
    return bool(_STOP.search(text or ""))


# --------------------------------------------------------------------------- the desktop
def _windows() -> list[tuple[str, str]]:
    """(window id, title) for everything on screen."""
    try:
        out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=6).stdout
    except Exception:  # noqa: BLE001
        return []
    found = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 4:
            found.append((parts[0], parts[3]))
    return found


def _worth_keeping(title: str) -> bool:
    low = title.lower()
    return any(keep in low for keep in KEEP)


def show_the_overview() -> None:
    """Press Super. Purely for the look of it — the desktop gathers itself before it clears."""
    input_.press_keys("super")
    time.sleep(1.1)
    input_.press_keys("escape")
    time.sleep(0.4)


def clear_the_desk() -> list[str]:
    """Ask everything that is not the work to close. Returns what was asked."""
    asked = []
    for wid, title in _windows():
        if _worth_keeping(title):
            continue
        try:
            subprocess.run(["wmctrl", "-i", "-c", wid], timeout=4, check=False)
            asked.append(title[:50])
        except Exception:  # noqa: BLE001
            continue
    return asked


def _screen() -> tuple[int, int]:
    try:
        out = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True,
                             text=True, timeout=5).stdout.split()
        return int(out[0]), int(out[1])
    except Exception:  # noqa: BLE001
        return 1920, 1080


def _place(match: str, x: int, y: int, w: int, h: int) -> bool:
    """Move and size the first window whose title contains `match`."""
    for wid, title in _windows():
        if match.lower() not in title.lower():
            continue
        try:
            # 0 removes any maximised state first, or the move is ignored.
            subprocess.run(["wmctrl", "-i", "-r", wid, "-b", "remove,maximized_vert,maximized_horz"],
                           timeout=4, check=False)
            subprocess.run(["wmctrl", "-i", "-r", wid, "-e", f"0,{x},{y},{w},{h}"],
                           timeout=4, check=False)
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


def lay_it_out() -> dict:
    """Editor down the left half; ChatGPT above a terminal on the right."""
    width, height = _screen()
    half, right = width // 2, width // 2
    top = height // 2
    placed = {
        "editor": _place("visual studio code", 0, 0, half, height),
        "chatgpt": _place("chatgpt", right, 0, half, top),
        "terminal": _place("terminal", right, top, half, height - top),
    }
    return placed


# --------------------------------------------------------------------------- the sting
def build_sting(seconds: float = STING_S, rate: int = 44100) -> bytes:
    """A short rising chord that resolves — the sound of something powering up.

    Synthesised from three fifths over a rising root, with a slow attack and a long tail. It is
    not the film's music and is not trying to be: a few seconds of that would be somebody's
    recording, and this needs no permission from anyone.
    """
    import numpy as np

    t = np.linspace(0, seconds, int(seconds * rate), endpoint=False)
    # The root climbs a fourth over the first half and settles.
    root = 110.0 * (1 + 0.33 * np.clip(t / (seconds * 0.55), 0, 1))
    phase = 2 * np.pi * np.cumsum(root) / rate
    chord = sum(w * np.sin(k * phase) for k, w in ((1, 0.55), (1.5, 0.3), (2, 0.28), (3, 0.14)))
    # A little shimmer high up, the part that reads as "system".
    chord += 0.05 * np.sin(2 * np.pi * 2200 * t) * np.clip(1 - t / seconds, 0, 1)
    attack = np.clip(t / 0.25, 0, 1)
    release = np.clip((seconds - t) / 0.9, 0, 1) ** 1.5
    wave = chord * attack * release
    wave = wave / (np.abs(wave).max() or 1) * 0.55
    return (wave * 32767).astype("<i2").tobytes()


def play_sting() -> bool:
    """Play it, without blocking the rest of the sequence."""
    import subprocess as sp
    import tempfile
    import wave as wavefile

    try:
        pcm = build_sting()
        path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        with wavefile.open(path, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(44100)
            f.writeframes(pcm)
        sp.Popen(["aplay", "-q", path], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        return True
    except Exception:  # noqa: BLE001 — no sting is not a reason to abandon the mode
        return False


# --------------------------------------------------------------------------- arming and standing down
ANNOUNCEMENT = "Iron Man mode activated."
STOOD_DOWN = "Back to normal, sir."


def activate() -> dict:
    """Run the whole sequence and report what actually happened at each step."""
    global _on

    show_the_overview()
    closed = clear_the_desk()
    time.sleep(1.2)                  # windows need a moment to go before the survivors are moved
    placed = lay_it_out()
    sting = play_sting()

    _on = State(started=time.time(), closed=tuple(closed))
    return {"closed": closed, "placed": placed, "sting": sting}


def deactivate() -> dict:
    global _on
    was, _on = _on, None
    return {"was_on": was is not None,
            "minutes": int((time.time() - was.started) / 60) if was else 0}
