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
from pathlib import Path
from typing import Optional

from ..integrations import desktop_control as input_

# Kept through the sweep. Everything else on screen is asked to go.
KEEP = ("code", "visual studio code", "jarvis", "spotify", "opera", "chrome", "chromium",
        "firefox", "terminal", "konsole", "alacritty", "kitty")

CHATGPT_URL = "https://chatgpt.com/"
CHATGPT_PROFILE = Path.home() / ".config/jarvis/chatgpt-chrome"
# Chrome may already have a native-Wayland process. Such a process accepts --app and exits zero,
# but its window is invisible to wmctrl. A small dedicated X11 profile makes the app window both
# deterministic and independently matchable; --no-first-run avoids the welcome window masking it.
CHATGPT_FLAGS = ("--ozone-platform=x11", f"--user-data-dir={CHATGPT_PROFILE}",
                 "--no-first-run", "--no-default-browser-check")
CHATGPT_COMMAND = ("google-chrome", *CHATGPT_FLAGS, f"--app={CHATGPT_URL}")
CHATGPT_RETRY_COMMAND = ("google-chrome", *CHATGPT_FLAGS, "--new-window",
                         f"--app={CHATGPT_URL}")
TERMINAL_COMMAND = ("x-terminal-emulator",)
TERMINAL_TITLE_MARKERS = ("terminal", "konsole", "alacritty", "kitty", "xterm")

# These are the same viewport fractions as --rail and --bar in overlay/ironman.css. Keep them
# numeric here because wmctrl accepts pixels, not CSS units.
RAIL_FRACTION = 0.126
BAR_FRACTION = 0.046
WINDOW_WAIT_S = 12.0

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
        return _place_window(wid, x, y, w, h)
    return False


def _place_window(wid: str, x: int, y: int, w: int, h: int) -> bool:
    """Place one window already identified from its real wmctrl title."""
    try:
        # Removing maximisation first matters: many desktops otherwise accept the move and ignore
        # it, which is indistinguishable from success by return code alone.
        subprocess.run(["wmctrl", "-i", "-r", wid, "-b", "remove,maximized_vert,maximized_horz"],
                       timeout=4, check=False)
        requested = [x, y, w, h]
        # GNOME/XWayland can interpret wmctrl's nominally absolute x/y as offsets. Do not trust
        # its zero exit status: observe the resulting rectangle and feed the measured error back
        # into the next request. On a normal X11 WM this exits after the first pass; on the laptop
        # this normally converges on the second. Chrome adds a frame offset as well, so retain two
        # observations and solve the resulting linear coordinate transform rather than assuming
        # every XWayland client scales in the same way.
        candidate = requested.copy()
        observed = None
        x_history: list[tuple[int, int]] = []
        y_history: list[tuple[int, int]] = []
        for _attempt in range(6):
            moved = subprocess.run(
                ["wmctrl", "-i", "-r", wid, "-e", "0," + ",".join(map(str, candidate))],
                timeout=4, check=False)
            if moved.returncode != 0:
                return False
            time.sleep(0.08)
            observed = _window_geometry(wid)
            if observed is None:     # wmctrl -lG unavailable: retain the old best-effort contract
                return True
            x_history.append((candidate[0], observed[0]))
            y_history.append((candidate[1], observed[1]))
            if all(abs(got - want) <= 2 for got, want in zip(observed, requested)):
                return True
            # GNOME reserves its top panel and some clients also report their decorated content
            # origin below the outer frame. When repeated moves prove that y is clamped, preserve
            # the requested bottom edge by shortening the window by only that unavoidable gap.
            top_gap = observed[1] - y
            y_is_clamped = (len(y_history) >= 2
                            and y_history[-1][0] != y_history[-2][0]
                            and abs(y_history[-1][1] - y_history[-2][1]) <= 2)
            constrained_top = (abs(observed[0] - x) <= 2 and abs(observed[2] - w) <= 2
                               and 0 < top_gap <= 96 and y_is_clamped)
            if constrained_top and abs(observed[1] + observed[3] - (y + h)) <= 2:
                return True
            # On a scaled XWayland desktop the EWMH bridge doubles positions but not sizes. Infer
            # that coordinate scale from what actually happened: 242 requested -> 484 observed
            # becomes 121 on the next attempt. Width and height use ordinary error feedback.
            candidate = [_coordinate_command(x_history, x),
                         _coordinate_command(y_history, y),
                         candidate[2] + w - observed[2],
                         h - top_gap if constrained_top else candidate[3] + h - observed[3]]
        return False
    except Exception:  # noqa: BLE001
        return False


def _coordinate_command(history: list[tuple[int, int]], wanted: int) -> int:
    """Infer the command coordinate for scaled or frame-offset XWayland clients."""
    sent, observed = history[-1]
    if len(history) >= 2:
        previous_sent, previous_observed = history[-2]
        if sent != previous_sent and observed != previous_observed:
            slope = (observed - previous_observed) / (sent - previous_sent)
            if abs(slope) > 0.05:
                intercept = observed - slope * sent
                return round((wanted - intercept) / slope)
    if sent and observed:
        return round(sent * wanted / observed)
    return sent + wanted - observed


def _window_geometry(wid: str) -> Optional[tuple[int, int, int, int]]:
    """Read (x, y, width, height) back from the window manager."""
    try:
        out = subprocess.run(["wmctrl", "-lG"], capture_output=True, text=True,
                             timeout=5).stdout
        for line in out.splitlines():
            parts = line.split(None, 7)
            if len(parts) >= 7 and parts[0].lower() == wid.lower():
                return tuple(int(value) for value in parts[2:6])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _find_window(markers: tuple[str, ...]) -> Optional[tuple[str, str]]:
    """Return the first window whose current title contains one of the markers."""
    lowered = tuple(marker.lower() for marker in markers)
    return next(((wid, title) for wid, title in _windows()
                 if any(marker in title.lower() for marker in lowered)), None)


def _find_terminal() -> Optional[tuple[str, str]]:
    """Recognise both named terminals and the user@host titles Ptyxis actually exposes."""
    named = _find_window(TERMINAL_TITLE_MARKERS)
    if named:
        return named
    return next(((wid, title) for wid, title in _windows()
                 if re.search(r"\b[^\s@]+@[^\s:]+:", title)), None)


def _launch(command: tuple[str, ...]) -> bool:
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_for_window(markers: tuple[str, ...] = (), *, new_since: set[str] | None = None,
                     timeout: float = WINDOW_WAIT_S) -> Optional[tuple[str, str]]:
    """Wait for a titled window, optionally accepting any window newly created by a launch."""
    deadline = time.monotonic() + timeout
    lowered = tuple(marker.lower() for marker in markers)
    while time.monotonic() < deadline:
        windows = _windows()
        if lowered:
            match = next(((wid, title) for wid, title in windows
                          if any(marker in title.lower() for marker in lowered)), None)
            if match:
                return match
        if new_since is not None:
            match = next(((wid, title) for wid, title in windows if wid not in new_since), None)
            if match:
                return match
        time.sleep(0.2)
    return None


def _inner_layout(width: int, height: int) -> dict[str, tuple[int, int, int, int]]:
    """Pixel rectangles inside the overlay's 12.6vw rails and 4.6vh bars."""
    rail = round(width * RAIL_FRACTION)
    bar = round(height * BAR_FRACTION)
    inner_w = max(1, width - 2 * rail)
    inner_h = max(1, height - 2 * bar)
    left_w = inner_w // 2
    right_w = inner_w - left_w
    top_h = inner_h // 2
    bottom_h = inner_h - top_h
    right_x = rail + left_w
    return {
        "editor": (rail, bar, left_w, inner_h),
        "chatgpt": (right_x, bar, right_w, top_h),
        "terminal": (right_x, bar + top_h, right_w, bottom_h),
    }


def lay_it_out() -> dict:
    """Launch missing work surfaces, then tile all three inside the overlay frame."""
    editor = _find_window(("visual studio code",))
    terminal = _find_terminal()
    chatgpt = _find_window(("chatgpt",))

    if terminal is None:
        before = {wid for wid, _title in _windows()}
        if _launch(TERMINAL_COMMAND):
            # Default terminal titles often name the shell or working directory, not "Terminal".
            # The new window ID lets us read and retain the title wmctrl actually reports.
            terminal = _wait_for_window(new_since=before)

    if chatgpt is None and _launch(CHATGPT_COMMAND):
        chatgpt = _wait_for_window(("chatgpt",))
        # A resident Chrome process can swallow a plain app request without producing a window.
        # Ask explicitly for a new window once before reporting the surface as missing.
        if chatgpt is None and _launch(CHATGPT_RETRY_COMMAND):
            chatgpt = _wait_for_window(("chatgpt",))

    width, height = _screen()
    rects = _inner_layout(width, height)
    windows = {"editor": editor, "chatgpt": chatgpt, "terminal": terminal}
    return {name: bool(window and _place_window(window[0], *rects[name]))
            for name, window in windows.items()}


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
