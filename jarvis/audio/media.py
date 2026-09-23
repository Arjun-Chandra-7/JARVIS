"""The video or song playing while the person talks to Jarvis.

Three jobs, all about not letting media and conversation step on each other:

* **Ducking.** When a conversation starts over a playing video, the other applications' audio
  streams are turned down — not paused — for as long as the conversation lasts, and put back
  exactly as they were when it ends. The video keeps its place and keeps playing; it just stops
  drowning out the person and Jarvis. Pausing is a change of state the person would have to
  notice and undo; a volume that comes back by itself is not.
* **Pause and resume on request.** "Pause it", "wait, pause", "play", "next" go straight to the
  player (MPRIS through playerctl) — a local action, no model. Jarvis remembers whether *it*
  paused something, and resumes only when asked.
* **Knowing whether anything is playing**, which barge-in and the wake word use to be stricter.

Streams are found with pw-dump and changed with wpctl; Jarvis's own streams (this process) and
the echo canceller's are never touched.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from typing import Optional

DUCK_TO = float(os.environ.get("JARVIS_DUCK_VOLUME", "0.3"))


def _run(args: list[str], timeout: float = 1.5) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def playing() -> bool:
    """Is a video or song playing aloud? (MPRIS: browsers, Spotify, players.)"""
    return "Playing" in _run(["playerctl", "-a", "status"], timeout=1.0)


# ------------------------------------------------------------------ transport
_TRANSPORT = {
    "pause": re.compile(r"(?i)^(?:(?:ok(?:ay)?|wait|jarvis|hey jarvis|arre|ruko)[,\s]+)*"
                        r"(?:pause|pause it|pause (?:the |this )?(?:video|song|music|lecture)|"
                        r"pause karo|pause kar do|rok do|video rok do)[.!]?$"),
    "play": re.compile(r"(?i)^(?:(?:ok(?:ay)?|jarvis)[,\s]+)*(?:play|resume|unpause|continue the video|"
                       r"resume (?:the |it|playback)|play it|chalao|chala do|resume karo)[.!]?$"),
    "next": re.compile(r"(?i)^(?:(?:ok(?:ay)?|jarvis)[,\s]+)*(?:next|skip|next (?:video|song|track)|"
                       r"skip (?:this|it))[.!]?$"),
    "previous": re.compile(r"(?i)^(?:(?:ok(?:ay)?|jarvis)[,\s]+)*(?:previous|go back|previous "
                           r"(?:video|song|track)|last (?:song|track))[.!]?$"),
}


def transport_command(text: str) -> Optional[str]:
    """"pause", "play", "next" or "previous" when the whole utterance is that, else None."""
    said = (text or "").strip()
    for name, pattern in _TRANSPORT.items():
        if pattern.match(said):
            return name
    return None


class Media:
    def __init__(self, run=_run) -> None:
        self._run = run
        self.paused_by_us = False
        self._ducked: dict[int, float] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------- transport
    def status(self) -> str:
        out = self._run(["playerctl", "status"]).strip()
        return out.splitlines()[0] if out else ""

    def transport(self, name: str) -> tuple[bool, str]:
        """Do it and check it. (done, what to say)."""
        if not self._run(["playerctl", "-l"]).strip():
            return False, "Nothing is playing that I can control, sir."
        before = self.status()
        verb = {"pause": "pause", "play": "play", "next": "next", "previous": "previous"}[name]
        self._run(["playerctl", verb])
        after = self.status()
        if name == "pause":
            if after == "Paused" or before != "Playing":
                self.paused_by_us = before == "Playing"
                return True, "Paused, sir." if before == "Playing" else "It's already paused, sir."
            return False, "I asked the player to pause, but it's still playing, sir."
        if name == "play":
            self.paused_by_us = False
            if after == "Playing":
                return True, "Playing, sir."
            return False, "I asked the player to play, but it didn't start, sir."
        return True, "Next, sir." if name == "next" else "Going back, sir."

    # -------------------------------------------------------------- ducking
    def _streams(self) -> list[int]:
        """Other applications' running playback streams."""
        try:
            nodes = json.loads(self._run(["pw-dump"], timeout=2.0) or "[]")
        except ValueError:
            return []
        me = str(os.getpid())
        out = []
        for node in nodes:
            info = node.get("info") or {}
            props = info.get("props") or {}
            if props.get("media.class") != "Stream/Output/Audio" or info.get("state") != "running":
                continue
            if str(props.get("application.process.id", "")) == me:
                continue
            if str(props.get("node.name", "")).startswith("jarvis_"):
                continue
            out.append(int(node["id"]))
        return out

    def duck(self) -> int:
        """Turn other applications down. Returns how many streams were ducked."""
        with self._lock:
            if self._ducked:
                return len(self._ducked)
            for node in self._streams():
                current = re.search(r"Volume:\s*([\d.]+)", self._run(["wpctl", "get-volume", str(node)]))
                if not current:
                    continue
                volume = float(current.group(1))
                if volume < 0.05:
                    continue
                self._ducked[node] = volume
                self._run(["wpctl", "set-volume", str(node), f"{DUCK_TO * volume:.2f}"])
            return len(self._ducked)

    def restore(self) -> int:
        """Put every ducked stream back to the volume it had. Returns how many."""
        with self._lock:
            ducked, self._ducked = self._ducked, {}
        for node, volume in ducked.items():
            self._run(["wpctl", "set-volume", str(node), f"{volume:.2f}"])
        return len(ducked)

    @property
    def ducked(self) -> bool:
        return bool(self._ducked)


MEDIA = Media()
