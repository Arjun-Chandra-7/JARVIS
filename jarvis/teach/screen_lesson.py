"""The screen-aware lesson: "pause and explain this step visually" over a video that is playing.

In order, and each step can end the lesson honestly:

  1. find the tab with the video (not whichever tab automation lands on);
  2. make sure it is a YouTube video, not an ad, not an error;
  3. pause it, and read the player back to confirm it stopped;
  4. read the title, the time, and the transcript around that time;
  5. decide the topic from those words — the transcript first, then the title — and say which;
  6. look at the frame: find where the video is on screen, take one screenshot of that region,
     look for a right-angled triangle, and delete the screenshot;
  7. teach — tracing the triangle if it was found clearly enough, otherwise on a clean one,
     saying which.

What the teacher said is never invented: the words "the teacher is on Pythagoras here" are used
only when the transcript window actually contains it. Nothing read from the screen is logged or
kept after the lesson.
"""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import layout, triangle
from .intents import topic_of
from .lessons import pythagoras
from .plan import LessonPlan, SourceContext

# The video element's place on screen, from the page itself. Firefox on Wayland is never told
# where its window is (screenX is 0), but mozInnerScreenX/Y give the viewport's offset inside the
# window, and a window whose outer size is the work area is maximised at the work area's corner.
_GEOMETRY = r"""
const w = window.wrappedJSObject || window;
const v = document.querySelector("video.html5-main-video") || document.querySelector("video");
if (!v) return {ok: false};
const r = v.getBoundingClientRect();
return {ok: true, x: r.x, y: r.y, w: r.width, h: r.height,
        innerX: window.mozInnerScreenX === undefined ? null : window.mozInnerScreenX,
        innerY: window.mozInnerScreenY === undefined ? null : window.mozInnerScreenY,
        screenX: window.screenX, screenY: window.screenY,
        outerW: window.outerWidth, outerH: window.outerHeight, innerW: window.innerWidth, innerH: window.innerHeight,
        dpr: window.devicePixelRatio, paused: v.paused, url: location.href,
        visible: document.visibilityState === "visible", full: !!document.fullscreenElement};
"""

SAY = {
    "no_page": {"en": "I can't see a browser I can read. Say “restart the browser with control” and ask again.",
                "hinglish": "Mujhe koi browser nahi dikh raha jo main padh sakun. “Restart the browser with control” bolo, phir poochho.",
                "hi": "मुझे कोई browser नहीं दिख रहा जो मैं पढ़ सकूँ। “Restart the browser with control” बोलो, फिर पूछो।",
                "hi-pure": "मुझे कोई ब्राउज़र नहीं दिख रहा जिसे मैं पढ़ सकूँ। ब्राउज़र को नियंत्रण के साथ फिर से खोलिए।"},
    "wrong_tab": {"en": "The tab in front isn't a YouTube video. Bring the video to the front and ask me again.",
                  "hinglish": "Saamne wala tab YouTube video nahi hai. Video wala tab saamne lao aur phir bolo.",
                  "hi": "सामने वाला tab YouTube video नहीं है। Video वाला tab सामने लाओ और फिर बोलो।",
                  "hi-pure": "सामने वाला टैब यूट्यूब वीडियो नहीं है। वीडियो वाला टैब सामने लाइए और फिर पूछिए।"},
    "ad": {"en": "An ad is playing. Ask me again once the lesson is back.",
           "hinglish": "Abhi ad chal raha hai. Lesson wapas aaye to phir bolo.",
           "hi": "अभी ad चल रहा है। Lesson वापस आए तो फिर बोलो।",
           "hi-pure": "अभी विज्ञापन चल रहा है। पाठ वापस आने पर फिर पूछिए।"},
    "player_error": {"en": "YouTube is showing an error on that video: {why}",
                     "hinglish": "YouTube is video pe error dikha raha hai: {why}",
                     "hi": "YouTube इस video पर error दिखा रहा है: {why}",
                     "hi-pure": "यूट्यूब इस वीडियो पर त्रुटि दिखा रहा है: {why}"},
    "no_pause": {"en": "I tried to pause the video but it's still playing, so I won't draw over it. {why}",
                 "hinglish": "Maine video pause karne ki koshish ki par woh abhi bhi chal raha hai, isliye main uske upar draw nahi karunga. {why}",
                 "hi": "मैंने video pause करने की कोशिश की पर वो अभी भी चल रहा है, इसलिए मैं उसके ऊपर draw नहीं करूँगा। {why}",
                 "hi-pure": "मैंने वीडियो रोकने की कोशिश की पर वह अभी भी चल रहा है, इसलिए मैं उस पर चित्र नहीं बनाऊँगा। {why}"},
    "blocked": {"en": "The browser refused to let me control the video: {why}",
                "hinglish": "Browser ne mujhe video control nahi karne diya: {why}",
                "hi": "Browser ने मुझे video control नहीं करने दिया: {why}",
                "hi-pure": "ब्राउज़र ने मुझे वीडियो नियंत्रित नहीं करने दिया: {why}"},
    "not_topic": {"en": "I've paused at {at}. This part looks like {title}, and I can only draw Pythagoras and RAG so far. Want me to explain it in words instead?",
                  "hinglish": "Maine {at} pe pause kiya. Yeh part {title} lagta hai, aur abhi main sirf Pythagoras aur RAG draw kar sakta hoon. Words mein samjhaun?",
                  "hi": "मैंने {at} पर pause किया। यह part {title} लगता है, और अभी मैं सिर्फ़ Pythagoras और RAG draw कर सकता हूँ। Words में समझाऊँ?",
                  "hi-pure": "मैंने {at} पर रोका। यह भाग {title} लगता है, और अभी मैं केवल पाइथागोरस और RAG के चित्र बना सकता हूँ। क्या शब्दों में समझाऊँ?"},
    "video_generic": {"en": "I've paused at {at}. Here's what this part is about, drawn out.",
                      "hinglish": "Maine {at} pe pause kiya. Yeh part diagram mein dekhte hain.",
                      "hi": "मैंने {at} पर pause किया। यह part diagram में देखते हैं।",
                      "hi-pure": "मैंने {at} पर रोका। इस भाग को चित्र में देखते हैं।"},
    "video_title_only_generic": {"en": "I've paused at {at}. I can't read captions for this part, so this is a general explanation of the video's topic, not what the teacher said.",
                                 "hinglish": "Maine {at} pe pause kiya. Is part ke captions nahi mil rahe, isliye yeh video ke topic ki general explanation hai, teacher ne kya bola woh nahi.",
                                 "hi": "मैंने {at} पर pause किया। इस part के captions नहीं मिल रहे, इसलिए यह video के topic की general explanation है, teacher ने क्या बोला वो नहीं।",
                                 "hi-pure": "मैंने {at} पर रोका। इस भाग के उपशीर्षक नहीं मिल रहे, इसलिए यह वीडियो के विषय की सामान्य व्याख्या है, शिक्षक के शब्द नहीं।"},
    "page": {"en": "Here's the part you're reading, drawn out.", "hinglish": "Jo aap padh rahe ho, woh diagram mein dekhte hain.",
             "hi": "जो आप पढ़ रहे हो, वो diagram में देखते हैं।", "hi-pure": "जो आप पढ़ रहे हैं, उसे चित्र में देखते हैं।"},
    "nothing_readable": {"en": "I can't read anything on the screen to explain. Select the part you mean, or open it in the browser, and ask again.",
                         "hinglish": "Screen pe mujhe padhne layak kuch nahi mila. Jo part chahiye use select karo, phir poochho.",
                         "hi": "Screen पर मुझे पढ़ने लायक कुछ नहीं मिला। जो part चाहिए उसे select करो, फिर पूछो।",
                         "hi-pure": "स्क्रीन पर पढ़ने योग्य कुछ नहीं मिला। जो भाग चाहिए उसे चुनिए, फिर पूछिए।"},
    "grounded": {"en": "I've paused at {at}. The teacher is on the Pythagoras theorem here.",
                 "hinglish": "Maine video {at} pe pause kar diya. Yahan teacher Pythagoras theorem padha rahe hain.",
                 "hi": "मैंने video {at} पर pause कर दिया। यहाँ teacher Pythagoras theorem पढ़ा रहे हैं।",
                 "hi-pure": "मैंने वीडियो {at} पर रोक दिया है। यहाँ शिक्षक पाइथागोरस प्रमेय पढ़ा रहे हैं।"},
    "title_only": {"en": "I've paused at {at}. I can't read captions for this part, so I'm going by the video's title: it's the Pythagoras theorem.",
                   "hinglish": "Maine {at} pe pause kiya. Is part ke captions nahi mil rahe, isliye video ke title se chal raha hoon: yeh Pythagoras theorem hai.",
                   "hi": "मैंने {at} पर pause किया। इस part के captions नहीं मिल रहे, इसलिए video के title से चल रहा हूँ: यह Pythagoras theorem है।",
                   "hi-pure": "मैंने {at} पर रोका। इस भाग के उपशीर्षक नहीं मिल रहे, इसलिए वीडियो के शीर्षक से चल रहा हूँ: यह पाइथागोरस प्रमेय है।"},
    "traced": {"en": "I'll mark it on the triangle on your screen.",
               "hinglish": "Main screen wale triangle pe hi mark karta hoon.",
               "hi": "मैं screen वाले triangle पर ही mark करता हूँ।",
               "hi-pure": "मैं आपकी स्क्रीन वाले त्रिभुज पर ही चिह्नित करता हूँ।"},
    "not_visible": {"en": "I can't see the video on your screen right now, so I'll draw a clean triangle here.",
                    "hinglish": "Video abhi screen pe nahi dikh raha, isliye main yahan ek saaf triangle banata hoon.",
                    "hi": "Video अभी screen पर नहीं दिख रहा, इसलिए मैं यहाँ एक साफ़ triangle बनाता हूँ।",
                    "hi-pure": "वीडियो अभी स्क्रीन पर नहीं दिख रहा, इसलिए मैं यहाँ एक साफ़ त्रिभुज बनाता हूँ।"},
    "clean": {"en": "I can't pick out the triangle in the frame clearly, so I'll draw a clean one beside it.",
              "hinglish": "Frame mein triangle saaf nahi dikh raha, isliye main side mein ek saaf triangle banata hoon.",
              "hi": "Frame में triangle साफ़ नहीं दिख रहा, इसलिए मैं side में एक साफ़ triangle बनाता हूँ।",
              "hi-pure": "चित्र में त्रिभुज साफ़ नहीं दिख रहा, इसलिए मैं बगल में एक साफ़ त्रिभुज बनाता हूँ।"},
}


def say(key: str, language: str, **kw) -> str:
    table = SAY[key]
    return table.get(language, table["en"]).format(**kw).strip()


@dataclass
class ScreenContext:
    """What was read for one lesson. Held in memory for the lesson, and dropped with it."""
    title: str = ""
    at_s: Optional[float] = None
    transcript: str = ""
    caption_language: str = ""
    topic: str = ""
    grounded_by: str = ""                 # "transcript" | "title" | ""
    video_rect: Optional[dict] = None     # logical px on the monitor
    rect_trusted: bool = False
    found: Optional[pythagoras.FoundTriangle] = None
    timings: dict = field(default_factory=dict)


@dataclass
class Material:
    """What is on the screen, as text, for a lesson on any subject — and how to say where it came from."""
    subject: str
    text: str
    intro: str
    source: SourceContext


@dataclass
class Outcome:
    lesson: Optional[LessonPlan] = None
    message: str = ""                     # said instead of a lesson, when there is none
    context: Optional[ScreenContext] = None
    page: Any = None
    material: Optional[Material] = None   # set when the caller should draw a lesson from it


def video_rect(geo: dict, area: dict) -> tuple[Optional[dict], bool]:
    """The video's rectangle in monitor logical pixels, and whether it can be trusted.

    Trusted only when the window's position is known: fullscreen (the video is the screen) or
    maximised (outer size equals the work area, so the window sits at its corner). Otherwise the
    rectangle is a guess and nothing is anchored to it."""
    if not geo or not geo.get("ok") or not geo.get("w") or not geo.get("h"):
        return None, False
    scale = float(area.get("scale") or 1.0)
    css = float(geo.get("dpr") or 1.0) / scale          # CSS px → logical px (page zoom included)
    work = area["work"]
    if geo.get("full"):
        return {"x": geo["x"] * css, "y": geo["y"] * css, "w": geo["w"] * css, "h": geo["h"] * css}, True
    ix, iy = geo.get("innerX"), geo.get("innerY")
    maximised = (abs(float(geo.get("outerW") or 0) - work["w"]) <= 2 and abs(float(geo.get("outerH") or 0) - work["h"]) <= 2)
    if ix is None or iy is None:
        return None, False
    ox, oy = (work["x"], work["y"]) if maximised else (float(geo.get("screenX") or 0), float(geo.get("screenY") or 0))
    rect = {"x": round(ox + ix + geo["x"] * css, 1), "y": round(oy + iy + geo["y"] * css, 1),
            "w": round(geo["w"] * css, 1), "h": round(geo["h"] * css, 1)}
    return rect, maximised


def _ground(title: str, transcript: str, chapter: str = "") -> tuple[str, str]:
    """(topic, grounded_by). The transcript window wins; the title is second best; else nothing."""
    t = topic_of(transcript)
    if t:
        return t, "transcript"
    t = topic_of(" ".join([title, chapter]))
    if t:
        return t, "title"
    return "", ""


def _capture_region(rect: dict, area: dict, capture: Optional[Callable[[str], Optional[str]]] = None):
    """One screenshot, cropped to ``rect``, as a numpy array. The file is deleted at once."""
    import cv2

    if capture is None:
        from ..vision import screenshot
        capture = screenshot._portal_screenshot if os.environ.get("WAYLAND_DISPLAY") else screenshot._tool_screenshot
    fd, path = tempfile.mkstemp(prefix="jarvis-teach-", suffix=".png")
    os.close(fd)
    try:
        got = capture(path)
        if not got or not os.path.exists(got):
            return None, None
        img = cv2.imread(got)
    finally:
        for p in {path, locals().get("got") or path}:
            try:
                os.unlink(p)
            except OSError:
                pass
    if img is None:
        return None, None
    h, w = img.shape[:2]
    # The screenshot is of the whole desktop in physical pixels; the monitor's logical size maps onto it.
    sx, sy = w / float(area["w"]), h / float(area["h"])
    x0, y0 = max(0, int(rect["x"] * sx)), max(0, int(rect["y"] * sy))
    x1, y1 = min(w, int((rect["x"] + rect["w"]) * sx)), min(h, int((rect["y"] + rect["h"]) * sy))
    if x1 - x0 < 40 or y1 - y0 < 40:
        return None, None
    crop = img[y0:y1, x0:x1].copy()
    del img
    return crop, (x1 - x0, y1 - y0)


def looks_like_player(crop) -> bool:
    """Is the YouTube player really in this part of the screenshot?

    The page can say its tab is visible while its window is on another workspace — found live:
    the "video" region of the screenshot was the desktop wallpaper. A paused YouTube player shows
    its red progress bar along the bottom of the video, so a run of that red in the bottom strip
    is the check. Without it nothing is traced."""
    import numpy as np

    h, w = crop.shape[:2]
    band = crop[int(h * 0.82):, :, :].astype(int)          # BGR
    b, g, r = band[..., 0], band[..., 1], band[..., 2]
    red = (r > 170) & (g < 70) & (b < 110)
    if not red.any():
        return False
    return _longest_run(red) >= max(12, int(w * 0.02))


def _longest_run(mask) -> int:
    """The longest horizontal run of True in a 2-D boolean array."""
    import numpy as np

    padded = np.zeros((mask.shape[0], mask.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    d = np.diff(padded, axis=1)
    starts = np.argwhere(d == 1)
    ends = np.argwhere(d == -1)
    return int((ends[:, 1] - starts[:, 1]).max()) if len(starts) else 0


def luminance(crop) -> float:
    try:
        return float(crop.mean()) / 255.0
    except Exception:  # noqa: BLE001
        return 0.3


def _legs(t: triangle.Triangle) -> tuple[tuple, tuple]:
    """(leg a end, leg b end): a is the more upright leg, as in a textbook figure."""
    def upright(p):
        dx, dy = abs(p[0] - t.right[0]), abs(p[1] - t.right[1])
        return dy / (dx + dy + 1e-6)
    return (t.a, t.b) if upright(t.a) >= upright(t.b) else (t.b, t.a)


def anchor_for(rect: dict, area: dict, confidence: float, window: str, source: str = "dom") -> dict:
    return {"id": "video", "source": source, "element_id": "video", "application": "browser",
            "window": window[:120] or "video", "bounds": {k: round(float(rect[k]), 1) for k in ("x", "y", "w", "h")},
            "confidence": round(max(0.0, min(1.0, confidence)), 3), "observed_at": round(time.time() * 1000, 1),
            "ttl_ms": 4000, "monitor": area.get("index", 0), "scale": float(area.get("scale") or 1.0),
            "tracking": "follow"}


async def prepare(language: str, area: dict, page=None, capture=None, want_topic: str = "",
                  transcript_timeout: float = 6.0) -> Outcome:
    """Everything up to the lesson plan. Never draws; the caller runs what comes back."""
    from ..screen import page as pages
    from ..screen.youtube import YouTube, as_text, clock, window

    ctx = ScreenContext()
    t0 = time.monotonic()
    if page is None:
        # What is in front: a YouTube tab takes the video path; a page or an app is read as text.
        # (find_video_page alone picked a background video while a textbook page was in front.)
        from .. import screen_context as sc
        try:
            front = await sc.locate(prefer_video=True)
        except Exception:  # noqa: BLE001
            front = sc.ScreenContext()
        if front.source == "youtube" and front.page is not None:
            page = front.page
        elif front.source in ("page", "app"):
            return await _text_material(front, language)
        else:
            page = await asyncio.to_thread(pages.find_video_page)
            if page is None:
                # A browser that cannot be driven is still a window with words in it: read it the
                # way the spoken explainer does (accessibility text, else OCR). Found live — "explain
                # the topic on my screen" over a Zen page said "I can't see a browser" while the
                # spoken answer to the same request read the page fine.
                try:
                    await sc.fill_from_app(front)
                except Exception:  # noqa: BLE001
                    pass
                if front.source == "app":
                    return await _text_material(front, language)
    if page is None:
        return Outcome(message=say("no_page", language))
    yt = YouTube(page)
    try:
        state = await yt.state()
    except Exception as e:  # noqa: BLE001
        return Outcome(message=say("blocked", language, why=str(e)[:120]))
    ctx.timings["state_ms"] = round((time.monotonic() - t0) * 1000)
    if not state.youtube:
        return Outcome(message=say("wrong_tab", language))
    if state.ad:
        return Outcome(message=say("ad", language))
    if state.player_error:
        return Outcome(message=say("player_error", language, why=state.player_error))
    if state.paused is not True:
        try:
            r = await yt.control("pause")
        except Exception as e:  # noqa: BLE001
            return Outcome(message=say("blocked", language, why=str(e)[:120]))
        if not r.get("verified"):
            return Outcome(message=say("no_pause", language, why=r.get("reason", "")))
        state.time = r.get("time", state.time)
        state.paused = True
    ctx.timings["paused_ms"] = round((time.monotonic() - t0) * 1000)
    ctx.title, ctx.at_s = state.title, state.time
    at = clock(state.time)

    try:
        segs, source = await asyncio.wait_for(yt.transcript(state), transcript_timeout)
    except Exception:  # noqa: BLE001 — slow or absent captions are "no transcript", not a failure
        segs, source = [], ""
    if segs and state.time is not None:
        ctx.transcript = as_text(window(segs, state.time, before=45, after=5))
        m = re.search(r"\(([\w-]+)\)", source or "")
        ctx.caption_language = m.group(1) if m else ""
    ctx.timings["transcript_ms"] = round((time.monotonic() - t0) * 1000)

    ctx.topic, ctx.grounded_by = _ground(state.title, ctx.transcript, state.chapter)
    if want_topic and not ctx.topic:
        ctx.topic = want_topic
    if ctx.topic != "pythagoras":
        # Any other subject: a lesson written from the captions (or, without them, a general one
        # on the video's topic that says it is not the teacher's words).
        src = SourceContext(kind="video", title=state.title, at_s=state.time, caption_language=ctx.caption_language,
                            transcript_used=bool(ctx.transcript))
        if ctx.transcript:
            text = f"Video: {state.title}\nPosition: {at}\nCaptions around this point:\n{ctx.transcript}"
            intro = say("video_generic", language, at=at)
            subject = f"what this part of the video \"{state.title}\" is teaching"
        else:
            text = f"Video: {state.title}\n{state.description[:500]}"
            intro = say("video_title_only_generic", language, at=at)
            subject = state.title or "this video's topic"
        return Outcome(context=ctx, page=page, material=Material(subject, text, intro, src))

    # The frame. Only with a trustworthy position — a triangle traced 200 px off is worse than a
    # clean one drawn beside the video.
    try:
        geo = await page.run(_GEOMETRY, timeout=5)
    except Exception:  # noqa: BLE001
        geo = None
    rect, trusted = video_rect(geo or {}, area)
    ctx.video_rect, ctx.rect_trusted = rect, trusted
    found = None
    palette = "jarvis"
    if rect and trusted:
        crop, size = await asyncio.to_thread(_capture_region, rect, area, capture)
        ctx.timings["frame_ms"] = round((time.monotonic() - t0) * 1000)
        if crop is not None and not looks_like_player(crop):
            ctx.timings["player_not_on_screen"] = 1
            crop = None
        if crop is not None:
            palette = "light" if luminance(crop) > 0.62 else "jarvis"
            t = await asyncio.to_thread(triangle.find, crop)
            del crop
            if t and t.confidence >= triangle.MIN_CONFIDENCE:
                s = triangle.to_screen(t, rect, size)
                leg_a, leg_b = _legs(s)
                found = pythagoras.FoundTriangle(s.right, leg_a, leg_b, s.confidence,
                                                 anchor_for(rect, area, s.confidence, state.title))
    ctx.found = found

    first = say("grounded" if ctx.grounded_by == "transcript" else "title_only", language, at=at)
    second = say("traced" if found else "not_visible" if ctx.timings.get("player_not_on_screen") else "clean", language)
    src = SourceContext(kind="video", title=state.title, at_s=state.time, caption_language=ctx.caption_language,
                        transcript_used=ctx.grounded_by == "transcript", triangle_from_screen=bool(found))
    plan = pythagoras.plan(language, area, found=found, source=src, intro=[first, second])
    plan.source_confidence = found.confidence if found else (0.9 if ctx.grounded_by == "transcript" else 0.6)
    if palette == "light":
        plan.setup[0]["theme"]["palette"] = "light"
    plan.extras["video_rect"] = rect
    ctx.timings["plan_ms"] = round((time.monotonic() - t0) * 1000)
    return Outcome(lesson=plan, context=ctx, page=page)


async def _text_material(front, language: str) -> Outcome:
    """A lesson from the page or app in front: the selection if there is one, else what is in view."""
    from .. import screen_context as sc

    title = front.title or front.window or "the screen"
    if front.source == "page" and front.page is not None:
        try:
            got = await sc.read_page(front.page, around_s=60.0)
        except Exception:  # noqa: BLE001
            got = {}
        text = got.get("selection") or got.get("in_view") or got.get("body") or ""
        title = got.get("title") or title
    else:
        text = front.selection or front.in_view or front.body
    text = (text or "").strip()
    if len(text) < 40:
        return Outcome(message=say("nothing_readable", language))
    src = SourceContext(kind="page" if front.source == "page" else "app", title=title[:120])
    return Outcome(material=Material(f"what the student is reading: {title[:120]}", f"{title}\n\n{text[:3000]}",
                                     say("page", language), src))


class AnchorTracker:
    """Keeps the video anchor current while a traced lesson is on screen.

    Every ``period`` seconds the page is asked where the video is. Moved (a scroll, a resize) →
    the anchor moves and the drawing with it. Playing again, another URL, the tab hidden, or the
    page unreachable → the anchor's confidence drops to nothing and the renderer hides the
    drawing: it described a frame that is no longer there. Stops with the lesson.
    """

    def __init__(self, page, overlay, area: dict, base: dict, url: str = "", period: float = 0.4) -> None:
        self.page, self.overlay, self.area, self.base, self.url = page, overlay, area, base, url
        self.period = period
        self.stop_event = threading.Event()
        self.updates = 0
        self.lost = ""

    def check(self, geo: Optional[dict]) -> Optional[dict]:
        """The anchor to send for one reading of the page (None: nothing to send)."""
        rect, trusted = video_rect(geo or {}, self.area)
        why = ""
        if not geo or not geo.get("ok"):
            why = "gone"
        elif geo.get("paused") is False:
            why = "playing"
        elif self.url and geo.get("url") and _strip_t(geo["url"]) != _strip_t(self.url):
            why = "navigated"
        elif geo.get("visible") is False:
            why = "hidden"
        elif not rect or not trusted:
            why = "untracked"
        if why:
            self.lost = why
            return anchor_for(self.base, self.area, 0.0, "video")
        self.updates += 1
        return anchor_for(rect, self.area, 0.95, "video")

    def run(self, still_active: Callable[[], bool]) -> None:
        import asyncio as _a

        loop = _a.new_event_loop()
        try:
            while not self.stop_event.wait(self.period) and still_active():
                try:
                    geo = loop.run_until_complete(self.page.run(_GEOMETRY, timeout=3))
                except Exception:  # noqa: BLE001
                    geo = None
                a = self.check(geo)
                if a:
                    try:
                        self.overlay.send([{"op": "anchor.attach", "anchor": a}])
                    except Exception:  # noqa: BLE001
                        pass
                if self.lost:
                    return
        finally:
            loop.close()

    def start(self, still_active: Callable[[], bool]) -> "AnchorTracker":
        threading.Thread(target=self.run, args=(still_active,), name="teach-anchor", daemon=True).start()
        return self

    def stop(self) -> None:
        self.stop_event.set()


def _strip_t(url: str) -> str:
    return re.sub(r"[&?]t=\d+s?", "", (url or "").split("#")[0])


__all__ = ["prepare", "Outcome", "ScreenContext", "AnchorTracker", "video_rect", "layout"]
