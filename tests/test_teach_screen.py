"""The screen-aware lesson, the triangle finder, phrase timing, and the overlay's own invariants.

The page is a fake that answers the same scripts a real YouTube tab would; the screenshot is a
synthetic frame written by the test. What is checked is what gets said and drawn in each
situation — including every way it can refuse.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

from jarvis.teach import screen_lesson, triangle
from jarvis.teach.lessons import pythagoras

ROOT = Path(__file__).resolve().parent.parent
AREA = {"index": 0, "scale": 1.0, "w": 1920, "h": 1080, "work": {"x": 0, "y": 28, "w": 1920, "h": 1052}}


def frame(kind="right", w=1920, h=1080, video=(191, 73, 1280, 720)):
    """A desktop screenshot with a whiteboard video where the page says the video is."""
    import cv2
    img = np.full((h, w, 3), 30, np.uint8)
    x, y, vw, vh = video
    img[y:y + vh, x:x + vw] = (240, 240, 236)
    ink = (25, 25, 25)
    if kind in ("right", "right+bar"):
        cv2.line(img, (x + 300, y + 600), (x + 300, y + 180), ink, 5)
        cv2.line(img, (x + 300, y + 600), (x + 900, y + 600), ink, 5)
        cv2.line(img, (x + 300, y + 180), (x + 900, y + 600), ink, 5)
    if kind.endswith("+bar"):
        cv2.line(img, (x + 10, y + vh - 20), (x + 700, y + vh - 20), (51, 0, 255), 4)   # YouTube's red progress bar
    return img


class FakePage:
    kind = "fake"

    def __init__(self, state=None, pause_ok=True, geo=None, transcript=None):
        self.state = {"youtube": True, "url": "https://www.youtube.com/watch?v=abc", "videoId": "abc", "fresh": True,
                      "title": "Pythagoras theorem | Class 10", "time": 252.0, "duration": 900.0, "paused": False,
                      "hasVideo": True, "chapter": "", "liveCaption": "", "tracks": [], "ad": False, "playerError": ""}
        self.state.update(state or {})
        self.pause_ok = pause_ok
        self.geo = {"ok": True, "x": 0, "y": 0, "w": 1280, "h": 720, "innerX": 191, "innerY": 45, "screenX": 0,
                    "screenY": 0, "outerW": 1920, "outerH": 1052, "innerW": 1721, "innerH": 999, "dpr": 1.0,
                    "paused": True, "url": self.state["url"], "visible": True, "full": False}
        self.geo.update(geo or {})
        self.transcript = transcript
        self.paused_calls = 0

    async def run(self, body, args=None, timeout=20.0):
        if "getBoundingClientRect" in body:
            return self.geo
        if "movie_player" in body and "getPlayerResponse" in body and "_CONTROL" not in body and args is None:
            return self.state
        if args and args[0] == "pause":
            self.paused_calls += 1
            return {"ok": True, "paused": self.pause_ok, "time": self.state["time"]}
        return None


@pytest.fixture()
def fake_transcript(monkeypatch):
    from jarvis.screen import youtube

    def install(text):
        async def tr(self, state):
            if text is None:
                return [], ""
            return [youtube.Segment(230.0, 30.0, text)], "captions (en)"
        monkeypatch.setattr(youtube.YouTube, "transcript", tr)
    return install


def run_prepare(page, capture=None, lang="en"):
    return asyncio.run(screen_lesson.prepare(lang, AREA, page=page, capture=capture))


def capture_of(img, seen):
    import cv2

    def cap(path):
        cv2.imwrite(path, img)
        seen.append(path)
        return path
    return cap


# --------------------------------------------------------------------------- the whole flow
def test_screen_lesson_pauses_verifies_grounds_and_traces(fake_transcript):
    fake_transcript("so by the Pythagoras theorem, a squared plus b squared is the hypotenuse squared")
    seen = []
    page = FakePage()
    out = run_prepare(page, capture_of(frame("right+bar"), seen))
    assert page.paused_calls == 1
    assert out.lesson is not None
    assert out.context.grounded_by == "transcript"
    assert out.lesson.source_context.transcript_used and out.lesson.source_context.triangle_from_screen
    intro = out.lesson.spoken_segments[:2]
    assert "4:12" in intro[0] and "teacher" in intro[0].lower()
    assert "triangle on your screen" in intro[1]
    C = out.lesson.extras["C"]
    # The right angle of the drawn triangle, in screen coordinates: video origin + 300, 600.
    assert abs(C[0] - (191 + 300)) < 8 and abs(C[1] - (28 + 45 + 600)) < 8
    assert seen and not any(os.path.exists(p) for p in seen)         # the screenshot is gone


def test_no_transcript_says_so_and_goes_by_the_title(fake_transcript):
    fake_transcript(None)
    out = run_prepare(FakePage(), capture_of(frame("none"), []))
    assert out.lesson is not None
    first = out.lesson.spoken_segments[0].lower()
    assert "can't read captions" in first and "title" in first and "teacher" not in first
    assert "clean triangle" in out.lesson.spoken_segments[1]
    assert not out.lesson.source_context.transcript_used


def test_wrong_tab_ad_error_and_unpausable_are_reported(fake_transcript):
    fake_transcript(None)
    assert "isn't a youtube video" in run_prepare(FakePage({"youtube": False})).message.lower()
    assert "ad is playing" in run_prepare(FakePage({"ad": True})).message
    assert "error" in run_prepare(FakePage({"playerError": "Sign in to confirm you're not a bot"})).message
    out = run_prepare(FakePage(pause_ok=False))
    assert out.lesson is None and "still playing" in out.message


def test_other_topics_are_not_forced_into_pythagoras(fake_transcript):
    fake_transcript("today we talk about photosynthesis and chlorophyll")
    out = run_prepare(FakePage({"title": "Photosynthesis explained"}))
    assert out.lesson is None and "Pythagoras and RAG" in out.message


def test_an_unmaximised_window_is_not_traced(fake_transcript):
    fake_transcript("Pythagoras theorem")
    seen = []
    out = run_prepare(FakePage(geo={"outerW": 1400, "outerH": 900, "screenX": 0}), capture_of(frame("right"), seen))
    assert out.lesson is not None and not out.lesson.source_context.triangle_from_screen
    assert not seen                                                  # no screenshot was even taken


def test_hindi_screen_lesson(fake_transcript):
    fake_transcript("पाइथागोरस प्रमेय में कर्ण का वर्ग")
    out = run_prepare(FakePage(), capture_of(frame("right+bar"), []), lang="hi-pure")
    assert out.lesson.language == "hi-pure"
    assert re.search(r"[ऀ-ॿ]", out.lesson.spoken_segments[0])


def test_video_rect_mapping_handles_zoom_and_fractional_scale():
    geo = {"ok": True, "x": 10, "y": 20, "w": 1000, "h": 500, "innerX": 100, "innerY": 40, "screenX": 0, "screenY": 0,
           "outerW": 1536, "outerH": 830, "dpr": 1.25, "full": False}
    area = {"index": 0, "scale": 1.25, "w": 1536, "h": 864, "work": {"x": 0, "y": 34, "w": 1536, "h": 830}}
    rect, trusted = screen_lesson.video_rect(geo, area)
    assert trusted and rect == {"x": 110.0, "y": 94.0, "w": 1000.0, "h": 500.0}
    geo2 = {**geo, "dpr": 1.5}                                       # the page zoomed to 120 %
    rect2, _ = screen_lesson.video_rect(geo2, area)
    assert rect2["w"] == 1200.0
    rect3, trusted3 = screen_lesson.video_rect({**geo, "outerW": 1000}, area)
    assert not trusted3


def test_anchor_tracker_follows_and_lets_go():
    t = screen_lesson.AnchorTracker(None, None, AREA, {"x": 191, "y": 73, "w": 1280, "h": 720},
                                    url="https://www.youtube.com/watch?v=abc")
    base = FakePage().geo
    moved = t.check({**base, "y": -200})                             # scrolled down 200 px
    assert moved["confidence"] > 0.9 and moved["bounds"]["y"] == 28 + 45 - 200
    assert t.check({**base, "paused": False})["confidence"] == 0.0 and t.lost == "playing"
    t2 = screen_lesson.AnchorTracker(None, None, AREA, {"x": 0, "y": 0, "w": 10, "h": 10}, url=base["url"])
    assert t2.check({**base, "url": "https://www.youtube.com/watch?v=zzz"})["confidence"] == 0.0
    assert t2.check(None)["confidence"] == 0.0


# --------------------------------------------------------------------------- triangles
def test_triangle_finder_accepts_right_triangles_only():
    import cv2
    board = np.full((540, 960, 3), 245, np.uint8)
    right = board.copy()
    cv2.polylines(right, [np.array([[200, 450], [200, 150], [620, 450]])], True, (20, 20, 20), 4)
    t = triangle.find(right)
    assert t and t.confidence >= triangle.MIN_CONFIDENCE and abs(t.right[0] - 200) < 6 and abs(t.right[1] - 450) < 6
    obtuse = board.copy()
    cv2.polylines(obtuse, [np.array([[150, 450], [700, 450], [300, 300]])], True, (20, 20, 20), 4)
    assert triangle.find(obtuse) is None
    small = board.copy()
    cv2.fillPoly(small, [np.array([[450, 250], [450, 290], [480, 270]])], (20, 20, 20))
    assert triangle.find(small) is None
    assert triangle.find(board) is None


# --------------------------------------------------------------------------- phrase timing
def test_speak_segments_reports_each_phrase_when_it_is_audible(monkeypatch):
    """on_start fires once per phrase, in order, at write time plus the stream's latency."""
    from jarvis.audio import local_tts

    writes = []

    class Stream:
        latency = 0.08

        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def write(self, block):
            writes.append(time.time())

        def stop(self):
            pass

        def abort(self):
            pass

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawOutputStream=Stream))
    monkeypatch.setattr(local_tts, "synth_stream", lambda text, *a, **k: iter([(b"\x01\x00" * 4000, 22050)]))
    from jarvis.audio import loudness
    monkeypatch.setattr(loudness, "sink_gain", lambda: 1.0)
    monkeypatch.setattr(loudness, "shape", lambda pcm, gain: pcm)
    starts, played = [], []
    local_tts.speak_segments(["One.", "Two, three.", "Four"], "x.onnx",
                             on_start=lambda i, at: starts.append((i, at, time.time())), on_played=played.append)
    assert [s[0] for s in starts] == [0, 1, 2]
    for _, at, now in starts:
        assert abs(at - (now * 1000 + 80)) < 15
    assert played == ["One.", "Two, three.", "Four"]


# --------------------------------------------------------------------------- the overlay's invariants
@pytest.fixture(scope="module")
def main_teach():
    return (ROOT / "overlay" / "teach" / "main-teach.js").read_text(encoding="utf-8")


def test_the_teaching_window_never_takes_focus_or_the_mouse(main_teach):
    assert "focusable: false" in main_teach
    assert re.search(r"win\.setIgnoreMouseEvents\(true\);\s*\n\s*win\.setAlwaysOnTop", main_teach)
    assert "sandbox: true" in main_teach and "nodeIntegration: false" in main_teach
    assert 'backgroundColor: "#00000000"' in main_teach
    # Hidden means unmapped and click-through, whatever state pen mode was in.
    hide = main_teach[main_teach.index("function hide()"):main_teach.index("// ---", main_teach.index("function hide()"))]
    assert "setIgnoreMouseEvents(true)" in hide and "win.hide()" in hide
    assert "PEN_CEILING_MS" in main_teach and "showInactive" in main_teach


def test_emergency_dismissal_does_not_wait(main_teach):
    body = main_teach[main_teach.index("function dismiss("):main_teach.index("// ---", main_teach.index("function dismiss("))]
    assert body.index("hide()") < body.index("post(")                 # hidden before anyone is told
    assert 'process.on("SIGURG"' in main_teach and "stopSpeech()" in body


def test_teach_page_runs_no_inline_or_remote_code():
    html = (ROOT / "overlay" / "teach.html").read_text(encoding="utf-8")
    csp = re.search(r'Content-Security-Policy"\s+content="([^"]+)"', html).group(1)
    assert "script-src 'self'" in csp and "unsafe" not in csp and "connect-src 'none'" in csp
    assert "<script>" not in html
    renderer = (ROOT / "overlay" / "teach" / "renderer.js").read_text(encoding="utf-8")
    assert "innerHTML" not in renderer and "eval(" not in renderer and "new Function" not in renderer


def test_main_process_logs_no_screen_content(main_teach):
    # Rejections name a field; nothing that was on the page or in a label is posted anywhere.
    assert 'why: String(e.path || "batch")' in main_teach
    assert "console.log(text" not in main_teach and "say(text" not in main_teach


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_geometry_under_node():
    script = r"""
const G = require(process.argv[1] + "/overlay/teach/geometry.js");
const out = {};
out.canvas = G.canvasSize(1920, 1052, 1.25);
out.anchor = G.anchorTransform({x: 100, y: 100, w: 200, h: 100}, {x: 100, y: -100, w: 200, h: 100});
out.border = G.borderPoint({x: 0, y: 0, w: 100, h: 50}, [300, 25], 0);
out.erase = G.strokeAt([{points: [[0,0],[100,0]], width: 4}, {points: [[0,50],[100,50]], width: 4}], [50, 48], 12);
out.miss = G.strokeAt([{points: [[0,0],[100,0]], width: 4}], [50, 80], 12);
out.ra = G.rightAngle([0,0],[0,-100],[100,0],20);
process.stdout.write(JSON.stringify(out));
"""
    done = subprocess.run(["node", "-e", script, str(ROOT)], capture_output=True, text=True, timeout=20)
    r = json.loads(done.stdout)
    assert r["canvas"] == {"w": 2400, "h": 1315, "ratio": 1.25}              # physical pixels at 125 %
    assert r["anchor"].startswith("translate(100 -100) scale(1 1) translate(-100 -100)")
    assert r["border"] == [100, 25]
    assert r["erase"] == 1 and r["miss"] == -1
    assert r["ra"] == "M0,-20L20,-20L20,0"


def test_a_region_without_the_player_is_not_traced(fake_transcript):
    """The tab says it is visible, but the window is on another workspace: the screenshot shows
    the wallpaper there. No red progress bar, no trace — found live."""
    fake_transcript("Pythagoras theorem, hypotenuse")
    img = frame("right")
    out = run_prepare(FakePage(), capture_of(img, []))
    assert not out.lesson.source_context.triangle_from_screen
    assert "can't see the video on your screen" in out.lesson.spoken_segments[1]
    import cv2
    x, y, vw, vh = 191, 73, 1280, 720
    cv2.line(img, (x + 10, y + vh - 20), (x + 700, y + vh - 20), (51, 0, 255), 4)    # the player's red bar
    out = run_prepare(FakePage(), capture_of(img, []))
    assert out.lesson.source_context.triangle_from_screen
