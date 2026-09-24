"""Talking to the teaching overlay: batches out, events back.

Out: every batch is validated (protocol.py), stamped with the lesson, a generation and a
sequence number, and posted to the backend's /emit as kind "teach". The overlay's main process
is subscribed to the same event stream as the HUD and validates it again.

Back: the overlay reports what it drew as kind "teach_event" — frames with their drift from the
spoken phrase, idle, pen mode, an emergency dismissal, a crashed renderer. A listener thread
follows the stream while a lesson is running and hands those to whoever asked.

Generations are how an old lesson is kept from touching a new one. The renderer drops any batch
from a generation older than the newest it has seen, and cancels anything an older generation
had scheduled; bumping the generation is therefore the whole of "forget what came before".

Nothing here logs what is drawn. The metrics kept are numbers: timings, counts, drift.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from .protocol import ProtocolError, validate

PORT = os.environ.get("JARVIS_WEB_PORT", "8770")


def _url(path: str) -> str:
    return f"http://127.0.0.1:{PORT}{path}"


class Transport:
    """Where batches go: the backend's /emit, from one sender thread, in order.

    Never on the caller's thread. Phrases are sent from the audio player's callback, and a POST
    that waited on a busy backend there held up the voice itself — found live as a 390 ms stall
    between two phrases. Now a slow backend makes a picture late, and never the speech.
    """

    def __init__(self) -> None:
        import queue

        self.q: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=512)
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None

    def post(self, kind: str, text: str) -> bool:
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._drain, name="teach-send", daemon=True)
                self.thread.start()
        try:
            self.q.put_nowait((kind, text))
            return True
        except Exception:  # noqa: BLE001 — a full queue means no overlay is listening; drop
            return False

    def _drain(self) -> None:
        import httpx

        with httpx.Client(timeout=1.5) as client:    # one keep-alive connection, not one per batch
            while True:
                kind, text = self.q.get()
                try:
                    client.post(_url("/emit"), json={"kind": kind, "text": text})
                except Exception:  # noqa: BLE001 — no backend means no overlay; the lesson still speaks
                    pass


class Metrics:
    """Numbers only: how late each picture was, relative to the words it belongs to."""

    def __init__(self, keep: int = 400) -> None:
        self.frames: deque = deque(maxlen=keep)
        self.anims: deque = deque(maxlen=keep)
        self.lock = threading.Lock()

    def add(self, evt: dict) -> None:
        with self.lock:
            self.frames.append({k: evt.get(k) for k in ("gen", "seq", "drift_ms", "cmd_ms", "e2e_ms")})

    def summary(self) -> dict:
        with self.lock:
            frames = list(self.frames)

        def stats(key):
            vals = sorted(abs(f[key]) for f in frames if isinstance(f.get(key), (int, float)))
            if not vals:
                return None
            return {"n": len(vals), "p50": vals[len(vals) // 2], "p95": vals[min(len(vals) - 1, int(len(vals) * 0.95))],
                    "max": vals[-1]}
        return {"drift_ms": stats("drift_ms"), "cmd_ms": stats("cmd_ms"), "e2e_ms": stats("e2e_ms")}


class Overlay:
    """The one handle a lesson uses to draw. Thread-safe; cheap when no overlay is running."""

    def __init__(self, transport: Optional[Transport] = None, clock: Callable[[], float] = time.time) -> None:
        self.transport = transport or Transport()
        self.clock = clock
        self.lock = threading.Lock()
        self.gen = int(clock() * 1000)
        self.seq = 0
        self.lesson = "idle"
        self.metrics = Metrics()
        self.listeners: list[Callable[[dict], None]] = []
        self.displays: list[dict] = []
        self.pen = False
        self.last_dismissed = 0.0
        self._listening = False

    # ------------------------------------------------------------------ out
    def new_generation(self, lesson: str) -> int:
        with self.lock:
            # Wall-clock milliseconds, never less than before: the voice process and the backend
            # each keep an Overlay, and whichever drew last must be the newest to the renderer.
            self.gen = max(self.gen + 1, int(self.clock() * 1000))
            self.seq = 0
            self.lesson = lesson
            return self.gen

    def envelope(self, cmds: list, at: Optional[float] = None, gen: Optional[int] = None) -> dict:
        with self.lock:
            self.seq += 1
            env: dict[str, Any] = {"v": 1, "lesson": self.lesson, "gen": self.gen if gen is None else gen,
                                   "seq": self.seq, "sent": round(self.clock() * 1000, 1), "cmds": list(cmds)}
        if at is not None:
            env["at"] = round(at, 1)
        return env

    def send(self, cmds: list, at: Optional[float] = None, gen: Optional[int] = None) -> bool:
        """Validate and send. ``at`` is wall-clock milliseconds when the batch should appear.

        Returns False when the batch was refused here (and so never sent) or could not be
        delivered. A refusal is a programming error in a lesson, and is raised in tests.
        """
        if not cmds:
            return True
        env = self.envelope(cmds, at, gen)
        validate(env)
        return self.transport.post("teach", json.dumps(env, ensure_ascii=False, separators=(",", ":")))

    def control(self, action: str, **extra) -> bool:
        return self.transport.post("teach_control", json.dumps({"action": action, **extra}))

    # ------------------------------------------------------------------ back
    def on_event(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        self.listeners.append(fn)
        self.listen()
        return lambda: self.listeners.remove(fn) if fn in self.listeners else None

    def dispatch(self, evt: dict) -> None:
        t = evt.get("type")
        if t == "frame":
            self.metrics.add(evt)
        elif t == "anim":
            with self.metrics.lock:
                self.metrics.anims.append({k: evt.get(k) for k in ("frames", "avg_frame_ms", "max_frame_ms")})
        elif t == "displays" and isinstance(evt.get("displays"), list):
            self.displays = evt["displays"]
        elif t == "pen":
            self.pen = bool(evt.get("on"))
        elif t == "dismissed":
            self.last_dismissed = self.clock()
        for fn in list(self.listeners):
            try:
                fn(evt)
            except Exception:  # noqa: BLE001 — one listener's bug must not stop the others
                pass

    def listen(self) -> None:
        """Follow the backend's event stream in a daemon thread, once."""
        if self._listening or isinstance(self.transport, _NullTransport):
            return
        self._listening = True
        threading.Thread(target=self._follow, name="teach-events", daemon=True).start()

    def _follow(self) -> None:
        import httpx

        backoff = 1.0
        while True:
            try:
                with httpx.stream("GET", _url("/events"), timeout=httpx.Timeout(5.0, read=None)) as r:
                    backoff = 1.0
                    for line in r.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            msg = json.loads(line[6:])
                        except ValueError:
                            continue
                        if msg.get("kind") != "teach_event":
                            continue
                        try:
                            evt = json.loads(msg.get("text") or "{}")
                        except ValueError:
                            continue
                        if isinstance(evt, dict):
                            self.dispatch(evt)
            except Exception:  # noqa: BLE001 — backend restarting; try again shortly
                pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    # ------------------------------------------------------------------ displays
    def _ask_overlay(self, wait_s: float = 0.6) -> list:
        """The overlay's own display list, work areas included — asked for and waited on."""
        if isinstance(self.transport, _NullTransport):
            return []
        got = threading.Event()
        off = self.on_event(lambda e: got.set() if e.get("type") == "displays" else None)
        try:
            deadline = time.monotonic() + wait_s + 1.0      # the listener may still be connecting
            while not got.is_set() and time.monotonic() < deadline:
                self.control("displays")
                got.wait(0.25)
        finally:
            off()
        return self.displays
    def work_area(self, monitor: Any = "primary") -> dict:
        """The monitor the scene goes on: its size and work area, in its own logical pixels."""
        ds = self.displays or _fetch_displays() or self._ask_overlay() or _mutter_displays()
        if ds:
            self.displays = ds
            if isinstance(monitor, int) and 0 <= monitor < len(ds):
                d = ds[monitor]
            else:
                d = next((d for d in ds if d.get("primary")), ds[0])
            b, w = d["bounds"], d.get("work") or d["bounds"]
            return {"index": d.get("index", 0), "scale": float(d.get("scale") or 1.0),
                    "w": b["width"], "h": b["height"],
                    "work": {"x": w["x"] - b["x"], "y": w["y"] - b["y"], "w": w["width"], "h": w["height"]}}
        return {"index": 0, "scale": 1.0, "w": 1920, "h": 1080, "work": {"x": 0, "y": 32, "w": 1920, "h": 1048}}


class _NullTransport(Transport):
    """For tests: remembers what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def post(self, kind: str, text: str) -> bool:
        self.sent.append((kind, json.loads(text)))
        return True

    def batches(self) -> list[dict]:
        return [b for k, b in self.sent if k == "teach"]

    def commands(self) -> list[dict]:
        return [c for b in self.batches() for c in b["cmds"]]


def _fetch_displays() -> list:
    import httpx

    try:
        r = httpx.get(_url("/teach/displays"), timeout=0.5)
        data = r.json() if r.status_code == 200 else {}
        return data.get("displays") or []
    except Exception:  # noqa: BLE001
        return []


def _mutter_displays() -> list:
    """GNOME's own idea of the monitors, when the overlay has not said. Logical coordinates."""
    import re
    import subprocess

    try:
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.DisplayConfig", "--object-path",
             "/org/gnome/Mutter/DisplayConfig", "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState"],
            capture_output=True, text=True, timeout=2).stdout
    except Exception:  # noqa: BLE001
        return []
    modes = re.findall(r"'(\d+)x(\d+)@[\d.]+', \d+, \d+, [\d.]+, [\d.]+, \[[^\]]*\], \{[^}]*'is-current'", out)
    logical = re.findall(r"\((-?\d+), (-?\d+), ([\d.]+), (?:uint32 )?\d+, (true|false), \[\(", out)
    ds = []
    for i, (x, y, scale, primary) in enumerate(sorted(logical, key=lambda t: (int(t[0]), int(t[1])))):
        w, h = (int(modes[i][0]), int(modes[i][1])) if i < len(modes) else (1920, 1080)
        s = float(scale) or 1.0
        bounds = {"x": int(x), "y": int(y), "width": round(w / s), "height": round(h / s)}
        ds.append({"index": i, "primary": primary == "true", "scale": s, "bounds": bounds, "work": None})
    return ds


OVERLAY = Overlay()


def overlay() -> Overlay:
    return OVERLAY


def use(o: Overlay) -> Overlay:
    """Swap the process-wide overlay (tests)."""
    global OVERLAY
    OVERLAY = o
    return o


__all__ = ["Overlay", "Transport", "ProtocolError", "overlay", "use", "OVERLAY"]
