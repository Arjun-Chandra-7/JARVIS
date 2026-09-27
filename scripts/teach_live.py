"""Drive the teaching overlay on the real desktop, with the real voice, and report the numbers.

    python scripts/teach_live.py rag|pyth|screen [--lang en] [--clock] [--shot DIR] [--shot-at S]
                                                [--interrupt-at S] [--follow "show where chunking happens"]
    python scripts/teach_live.py clear

Speech goes through the same local TTS the voice session uses (speak_segments), so drift is
measured against real audio: the renderer reports each picture's frame time against the moment
its phrase was handed to the sound device plus the device's latency. ``--interrupt-at`` stops the
voice the way a barge-in does, to check that speech and pictures stop together.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.teach import assistant  # noqa: E402
from jarvis.teach.bus import overlay  # noqa: E402
from jarvis.teach.runner import ClockSpeaker, Spoken, runner  # noqa: E402


class LocalVoice:
    """The local TTS as a lesson speaker — what the voice session uses, without the microphone."""

    def __init__(self) -> None:
        from jarvis.config import CONFIG
        self.model = CONFIG.piper_model
        self.device = CONFIG.audio_output_device
        self.stop_event = threading.Event()

    def speak(self, phrases, on_start):
        from jarvis.audio import local_tts
        self.stop_event = threading.Event()
        started, played = [], []
        local_tts.speak_segments(list(phrases), self.model, self.device, stop_event=self.stop_event,
                                 on_start=lambda i, at: (started.append(i), on_start(i, at)), on_played=played.append)
        return Spoken(len(started), len(played), len(played) < len(phrases))

    def stop(self):
        self.stop_event.set()


def shoot(out: Path, name: str) -> None:
    """A screenshot cropped to what the lesson drew — never the rest of the desktop, which may
    hold anything. Scaled from logical to screenshot pixels."""
    from PIL import Image
    from jarvis.vision import screenshot
    out.mkdir(parents=True, exist_ok=True)
    full = str(out / f".{name}-full.png")
    p = screenshot._portal_screenshot(full)
    try:
        # A copy: the lesson is still drawing while this reads it.
        scene = runner().scene
        ids = list(dict(scene.objects))
        boxes = [b for b in (scene.bounds_of(i) for i in ids) if b]
        if p and boxes:
            area = overlay().work_area()
            with Image.open(p) as im:
                sx = im.size[0] / area["w"]
                x0 = max(0, min(b[0] for b in boxes) - 40); y0 = max(0, min(b[1] for b in boxes) - 40)
                x1 = max(b[0] + b[2] for b in boxes) + 40; y1 = max(b[1] + b[3] for b in boxes) + 40
                im.crop((int(x0 * sx), int(y0 * sx), int(x1 * sx), int(y1 * sx))).save(out / f"{name}.png")
    finally:
        Path(full).unlink(missing_ok=True)
    print(json.dumps({"shot": str(out / f"{name}.png")}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--clock", action="store_true")
    ap.add_argument("--shot")
    ap.add_argument("--shot-at", type=float, action="append", default=[])
    ap.add_argument("--interrupt-at", type=float)
    ap.add_argument("--follow", action="append", default=[])
    ap.add_argument("--leave", action="store_true")
    ap.add_argument("--request", help="any request, e.g. \"explain the water cycle with a diagram\" (what: any)")
    ap.add_argument("--dismiss-at", type=float, help="send the emergency signal (SIGURG) to the overlay")
    ap.add_argument("--continue", dest="cont", action="store_true", help="after an interruption, say continue")
    a = ap.parse_args()
    ov = overlay()
    ov.listen()
    time.sleep(0.5)
    if a.what == "clear":
        runner().clear()
        return
    speaker = ClockSpeaker() if a.clock else LocalVoice()
    request = {"rag": {"en": "Explain RAG architecture with a diagram", "hinglish": "RAG ko diagram bana ke samjhao",
                       "hi": "RAG को diagram बनाकर समझाओ", "hi-pure": "RAG चित्र बनाकर शुद्ध हिंदी में समझाओ"},
               "pyth": {"en": "Explain the Pythagoras theorem with a diagram", "hinglish": "Pythagoras theorem diagram bana ke samjhao",
                        "hi": "Pythagoras theorem को diagram बनाकर समझाओ", "hi-pure": "पाइथागोरस प्रमेय चित्र बनाकर शुद्ध हिंदी में समझाओ"},
               "screen": {"en": "Jarvis, pause and explain this step visually", "hinglish": "Jarvis, pause karke isko diagram se samjhao",
                          "hi": "Jarvis, pause करके इसे चित्र बनाकर समझाओ", "hi-pure": "रोककर इसे चित्र बनाकर शुद्ध हिंदी में समझाओ"}}
    said = a.request or request[a.what][a.lang]
    t0 = time.time()
    timers = []
    if a.shot:
        for s in a.shot_at:
            timers.append(threading.Timer(s, shoot, (Path(a.shot), f"{a.what}-{a.lang}-{s:g}s")))
    marks = {}
    if a.interrupt_at:
        def cut():
            marks["interrupt_sent"] = time.time()
            speaker.stop()
        timers.append(threading.Timer(a.interrupt_at, cut))
    if a.dismiss_at:
        import os
        import signal

        def dismiss():
            marks["dismiss_sent"] = time.time()
            os.kill(int(open("/tmp/jarvis-overlay.pid").read()), signal.SIGURG)
        timers.append(threading.Timer(a.dismiss_at, dismiss))
        ov.on_event(lambda e: marks.setdefault("dismissed_event", (time.time(), e.get("ms"))) if e.get("type") == "dismissed" else None)
    for t in timers:
        t.start()
    ov.on_event(lambda e: marks.setdefault("first_frame", time.time()) if e.get("type") == "frame" else None)
    t_req = time.time()
    rep = asyncio.run(assistant.handle(said, speaker=speaker))
    r = runner()
    if "first_frame" in marks:
        print(json.dumps({"request_to_first_frame_ms": round((marks["first_frame"] - t_req) * 1000)}))
    print(json.dumps({"request": said, "handled": rep is not None, "status": r.status, "seconds": round(time.time() - t0, 1),
                      "said_chars": len(rep.text) if rep else 0, "message": rep.text[:200] if rep and not rep.spoken else ""}))
    marks["speech_returned"] = time.time()
    for k in ("interrupt_sent", "dismiss_sent"):
        if k in marks:
            print(json.dumps({k: True, "speech_stopped_after_ms": round((marks["speech_returned"] - marks[k]) * 1000),
                              "overlay_hide_ms": (marks.get("dismissed_event") or (0, None))[1],
                              "status": r.status, "pos": r.pos}))
    if a.cont:
        time.sleep(1.5)
        t1 = time.time()
        rep = asyncio.run(assistant.handle("continue", speaker=speaker))
        print(json.dumps({"continued": rep is not None, "seconds": round(time.time() - t1, 1), "status": r.status}))
    if a.leave:
        r.leave()
    for f in a.follow:
        t1 = time.time()
        rep = asyncio.run(assistant.handle(f, speaker=speaker))
        print(json.dumps({"follow": f, "handled": rep is not None, "seconds": round(time.time() - t1, 1)}))
    time.sleep(1.0)
    print(json.dumps({"metrics": ov.metrics.summary(), "anims": list(ov.metrics.anims)[-6:], "log": r.log[-6:]}))


if __name__ == "__main__":
    main()
