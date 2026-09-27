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
    def __init__(self, run=_run, sleep=None) -> None:
        import time

        self._run = run
        self._sleep = sleep or time.sleep
        self.paused_by_us = False
        self._paused_players: list[str] = []
        self._ducked: dict[int, float] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------- transport
    def players(self) -> dict[str, str]:
        """{player: status} for every MPRIS player."""
        names = [n.strip() for n in self._run(["playerctl", "-l"]).splitlines() if n.strip()]
        return {n: (self._run(["playerctl", "-p", n, "status"]).strip().splitlines() or [""])[0]
                for n in names}

    def status(self) -> str:
        states = self.players().values()
        return "Playing" if "Playing" in states else ("Paused" if "Paused" in states else "")

    def _settle(self, names: list[str], wanted: str) -> bool:
        """MPRIS reports a change a beat after it happens: look again for up to 0.6 s."""
        for _ in range(6):
            if all(self._run(["playerctl", "-p", n, "status"]).strip() == wanted for n in names):
                return True
            self._sleep(0.1)
        return False

    def transport(self, name: str) -> tuple[bool, str]:
        """Do it to the player that is actually playing, and check it. (done, what to say).

        Found on the end-to-end run: "pause it" went to playerctl's default player — which was
        not the one playing — and was checked before the player had reported the change.
        """
        players = self.players()
        if not players:
            return False, "Nothing is playing that I can control, sir."
        playing = [n for n, st in players.items() if st == "Playing"]
        if name == "pause":
            if not playing:
                return True, "Nothing I can control is playing, sir."
            for n in playing:
                self._run(["playerctl", "-p", n, "pause"])
            if self._settle(playing, "Paused"):
                self.paused_by_us, self._paused_players = True, playing
                return True, "Paused, sir."
            return False, "I asked the player to pause, but it's still playing, sir."
        if name == "play":
            targets = [n for n in self._paused_players if n in players] or \
                [n for n, st in players.items() if st == "Paused"][:1]
            if not targets:
                return (True, "It's already playing, sir.") if playing else \
                    (False, "There's nothing paused for me to play, sir.")
            for n in targets:
                self._run(["playerctl", "-p", n, "play"])
            self.paused_by_us, self._paused_players = False, []
            if self._settle(targets, "Playing"):
                return True, "Playing, sir."
            return False, "I asked the player to play, but it didn't start, sir."
        target = playing[:1] or list(players)[:1]
        self._run(["playerctl", "-p", target[0], "next" if name == "next" else "previous"])
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
