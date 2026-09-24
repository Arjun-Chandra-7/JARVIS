"""The teaching overlay's front door: one request in, a lesson, a control or a drawing out.

Called from two places:
  * the voice session, before anything else interprets the words, with a speaker that speaks
    phrase by phrase and reports when each is heard — the synchronised path;
  * the backend's command chain, for typed requests, with a clock instead of a voice, run in the
    background so the reply comes back at once and the drawing follows at reading pace.

Returns None whenever the request is not for the overlay, so every other handler still sees it.
"""
from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass
from typing import Optional

from . import intents, layout
from .bus import overlay
from .lessons import pythagoras, rag
from .plan import Phrase, Step
from .runner import ClockSpeaker, LessonRunner, Speaker, runner
from .scene import add, anim, focus, highlight, obj, update


@dataclass
class Reply:
    text: str
    spoken: bool = False          # already said, phrase by phrase, by the speaker


ACK = {
    "paused": {"en": "Paused.", "hinglish": "Ruk gaya.", "hi": "रुक गया।", "hi-pure": "रोक दिया।"},
    "cleared": {"en": "Cleared.", "hinglish": "Saaf kar diya.", "hi": "साफ़ कर दिया।", "hi-pure": "साफ़ कर दिया।"},
    "left": {"en": "I'll leave it up.", "hinglish": "Theek hai, rehne deta hoon.", "hi": "ठीक है, रहने देता हूँ।", "hi-pure": "ठीक है, रहने देता हूँ।"},
    "nothing": {"en": "There's nothing on the overlay to do that to.", "hinglish": "Overlay pe abhi kuch nahi hai.",
                "hi": "Overlay पर अभी कुछ नहीं है।", "hi-pure": "अभी स्क्रीन पर कुछ नहीं बना है।"},
    "undone": {"en": "Undone.", "hinglish": "Undo kar diya.", "hi": "Undo कर दिया।", "hi-pure": "पिछला बदलाव हटा दिया।"},
    "redone": {"en": "Redone.", "hinglish": "Redo kar diya.", "hi": "Redo कर दिया।", "hi-pure": "फिर से कर दिया।"},
    "pen_on": {"en": "Pen's on. Draw with the mouse; press Done when you're finished.",
               "hinglish": "Pen on hai. Mouse se draw karo, ho jaaye to Done dabao.",
               "hi": "Pen on है। Mouse से draw करो, हो जाए तो Done दबाओ।", "hi-pure": "पेन चालू है। माउस से बनाइए, और हो जाने पर Done दबाइए।"},
    "pen_off": {"en": "Pen's off.", "hinglish": "Pen off.", "hi": "Pen off.", "hi-pure": "पेन बंद।"},
    "which": {"en": "Which one? Say what to point at, or draw something first.",
              "hinglish": "Kaunsa? Batao kis cheez pe, ya pehle kuch draw karo.",
              "hi": "कौन सा? बताओ किस चीज़ पर, या पहले कुछ draw करो।", "hi-pure": "कौन सा? बताइए किस पर, या पहले कुछ बनाइए।"},
    "arrow_which": {"en": "An arrow from what to what? Draw or name two things first.",
                    "hinglish": "Arrow kahan se kahan tak? Pehle do cheezein draw karo.",
                    "hi": "Arrow कहाँ से कहाँ तक? पहले दो चीज़ें draw करो।", "hi-pure": "तीर कहाँ से कहाँ तक? पहले दो चीज़ें बनाइए।"},
    "done_drawing": {"en": "Done.", "hinglish": "Ho gaya.", "hi": "हो गया।", "hi-pure": "हो गया।"},
    "no_more": {"en": "That was the last step.", "hinglish": "Yeh aakhri step tha.", "hi": "यह आख़िरी step था।", "hi-pure": "यह अंतिम चरण था।"},
}

PY_FU = {
    "example": {"en": ["Another one: if a is 6 and b is 8,", "then 36 plus 64 is 100,", "so c is 10."],
                "hinglish": ["Ek aur: agar a chhe hai aur b aath,", "to chhattis plus chaunsath, sau,", "matlab c das hai."],
                "hi": ["एक और: अगर a छह है और b आठ,", "तो छत्तीस plus चौंसठ, सौ,", "मतलब c दस है।"],
                "hi-pure": ["एक और: अगर लम्ब छह और आधार आठ है,", "तो छत्तीस जोड़ चौंसठ, सौ,", "यानी कर्ण दस है।"]},
    "right": {"en": "Yes! 36 plus 64 is 100, and the square root of 100 is 10.",
              "hinglish": "Bilkul sahi! Chhattis plus chaunsath sau, aur sau ka square root das.",
              "hi": "बिल्कुल सही! छत्तीस plus चौंसठ सौ, और सौ का square root दस।",
              "hi-pure": "बिल्कुल सही! छत्तीस जोड़ चौंसठ सौ, और सौ का वर्गमूल दस।"},
    "wrong": {"en": "Not quite. 36 plus 64 is 100, so c is the square root of 100, which is 10.",
              "hinglish": "Thoda sa galat. Chhattis plus chaunsath sau hai, to c hai sau ka square root, yaani das.",
              "hi": "थोड़ा सा ग़लत। छत्तीस plus चौंसठ सौ है, तो c है सौ का square root, यानी दस।",
              "hi-pure": "थोड़ा गलत। छत्तीस जोड़ चौंसठ सौ है, तो कर्ण सौ का वर्गमूल है, यानी दस।"},
}


def _say(key: str, lang: str) -> str:
    return ACK[key].get(lang, ACK[key]["en"])


def language_of(text: str) -> str:
    from ..video_command import reply_language
    return reply_language(text)


CLEARING = ("clear", "hide", "clear_mine")


def on_screen() -> bool:
    """Whether the overlay shows anything at all — whichever process drew it. Found live: a lesson
    left up by a process that had exited could not be cleared by voice, because the voice's own
    runner had drawn nothing and so had nothing to clear."""
    return overlay().is_visible()


def wants(text: str, r: Optional[LessonRunner] = None) -> bool:
    """Cheap first look: could this be for the overlay at all?"""
    r = r or runner()
    if intents.lesson(text) or intents.draw(text):
        return True
    c = intents.control(text)
    if c and c.name in ("pen_on", "pen_off"):
        return True
    busy = r.active or not r.scene.empty()
    if busy and c:
        return True
    if c and c.name in CLEARING and on_screen():
        return True                    # drawn by another process, or one that has gone: still clearable
    if busy and r.lesson and r.lesson.extras.get("generic"):
        return about_the_lesson(text)
    return bool(busy and r.lesson and intents.follow_up(text, r.lesson.topic))


_LIVE_OR_ACTION = re.compile(
    r"(?i)\b(?:time|date|today|tomorrow|weather|temperature|news|score|price|battery|volume|brightness|"
    r"message|whatsapp|call|email|mail|remind|reminder|timer|alarm|open|play|pause|stop|close|search|"
    r"send|text\s+him|text\s+her|song|music|video\s+chala)\b|समय|मौसम|मैसेज|कॉल")
_QUESTIONISH = re.compile(
    r"(?i)\?|\b(?:what|why|how|which|who|when|where|explain|again|example|examples|simpler|difference|mean|means|"
    r"kya|kyu|kyun|kaise|kaun|samjha\w*|batao|dobara|phir\s+se|udaharan)\b|क्या|क्यों|कैसे|कौन|समझा|बताओ|दोबारा|उदाहरण|"
    r"\b(?:in|mein|me)\s+(?:english|hindi|hinglish)\b")


def about_the_lesson(text: str) -> bool:
    """A question that belongs to the lesson on screen — not an action, not a live fact."""
    s = (text or "").strip()
    if not s or len(s.split()) > 30 or _LIVE_OR_ACTION.search(s):
        return False
    return bool(_QUESTIONISH.search(s))


async def _generic_follow_up(said: str, lesson, lang: str) -> Optional[Step]:
    from ..llm import complete_detailed
    from ..video_command import _LANGUAGE_INSTRUCTION
    from .lessons import generic

    if re.search(r"(?i)\b(?:again|explain|what\s+(?:is|does)|tell\s+me\s+about|samjhao|dobara|phir\s+se|kya\s+hai)\b|समझाओ|दोबारा|क्या\s+है", said):
        step = generic.explain_again(lesson, said)
        if step is not None and lang == lesson.language:
            return step
    done = await complete_detailed(generic.FOLLOW_SYSTEM,
                                   generic.follow_up_prompt(lesson, said, _LANGUAGE_INSTRUCTION.get(lang, _LANGUAGE_INSTRUCTION["en"])),
                                   None, temperature=0.3, timeout=30.0, strength="strong")
    return generic.follow_up_step(lesson, done.text) if done.ok else None


# --------------------------------------------------------------------------- running
async def _run(fn, *args, background: bool = False):
    if background:
        threading.Thread(target=fn, args=args, daemon=True, name="teach-lesson").start()
        return None
    return await asyncio.to_thread(fn, *args)


def _pythagoras_follow_up(kind: str, lesson, lang: str, correct: Optional[bool] = None) -> Optional[Step]:
    W = pythagoras.words(lang)
    if kind == "hypotenuse":
        return Step("fu-hyp", "The hypotenuse", [Phrase(W["s3"][0], [focus("side-c", "lab-c", "py-ra", "py-fill"),
                                                                     highlight("side-c", "attention", True, 3000)])])
    if kind == "example":
        ex = PY_FU["example"].get(lang, PY_FU["example"]["en"])
        lab = ("लम्ब = 6", "आधार = 8", "कर्ण = 10") if lang == "hi-pure" else ("a = 6", "b = 8", "c = 10")
        return Step("fu-example", "Another example", [
            Phrase(ex[0], [update("lab-a", text=lab[0], style={"color": "attention"}), update("lab-b", text=lab[1], style={"color": "attention"}),
                           highlight("side-a", "attention", True, 1400), highlight("side-b", "attention", True, 1400)]),
            Phrase(ex[1], ([update("ex-sum", text="6² + 8² = 36 + 64 = 100")] if "ex-sum" in runner().scene.objects else [])),
            Phrase(ex[2], [update("lab-c", text=lab[2], style={"color": "confirm"}), highlight("lab-c", "confirm", True, 1800)])])
    if kind == "answer":
        text = PY_FU["right" if correct else "wrong"].get(lang, PY_FU["right" if correct else "wrong"]["en"])
        return Step("fu-answer", "Check", [Phrase(text, [highlight("side-c", "confirm" if correct else "attention", True, 2400)])])
    return None


NO_LESSON = {
    "en": "I couldn't put a diagram together for that just now — {why}",
    "hinglish": "Abhi iska diagram nahi ban paaya — {why}",
    "hi": "अभी इसका diagram नहीं बन पाया — {why}",
    "hi-pure": "अभी इसका चित्र नहीं बन पाया — {why}",
}
WHY = {
    "no_model": {"en": "no model is reachable.", "hinglish": "koi model reachable nahi hai.", "hi": "कोई model reachable नहीं है।",
                 "hi-pure": "कोई मॉडल उपलब्ध नहीं है।"},
    "unusable": {"en": "the lesson that came back didn't make sense, so I've drawn nothing rather than something wrong.",
                 "hinglish": "jo lesson aaya woh theek nahi tha, isliye galat cheez draw nahi ki.",
                 "hi": "जो lesson आया वो ठीक नहीं था, इसलिए ग़लत चीज़ draw नहीं की।",
                 "hi-pure": "जो पाठ आया वह ठीक नहीं था, इसलिए गलत चित्र नहीं बनाया।"},
}


async def generic_plan(subject: str, lang: str, area: dict, source_text: str = "", intro=None, src=None):
    """(plan, why-not). The model writes the lesson as JSON; generic.check keeps only what can be drawn."""
    from ..llm import complete_detailed
    from ..video_command import _LANGUAGE_INSTRUCTION
    from .lessons import generic

    done = await complete_detailed(generic.SYSTEM, generic.prompt(subject, _LANGUAGE_INSTRUCTION.get(lang, _LANGUAGE_INSTRUCTION["en"]), source_text),
                                   None, temperature=0.3, timeout=45.0, strength="strong")
    if not done.ok:
        return None, "no_model"
    spec = generic.check(generic.parse_json(done.text))
    if spec is None:
        return None, "unusable"
    return generic.build(spec, lang, area, subject, intro=intro, source=src), ""


def _no_lesson(lang: str, why: str) -> str:
    return NO_LESSON.get(lang, NO_LESSON["en"]).format(why=WHY[why].get(lang, WHY[why]["en"]))


async def _ack(speaker: Speaker, lang: str, background: bool) -> None:
    """Something said at once, while the lesson is being written — silence reads as nothing happening."""
    if background:
        return
    from .lessons.generic import ACK
    await asyncio.to_thread(speaker.speak, [ACK.get(lang, ACK["en"])], lambda i, at: None)


async def _lesson(intent: intents.Intent, text: str, speaker: Speaker, lang: str, background: bool) -> Reply:
    from . import screen_lesson

    r = runner()
    area = await asyncio.to_thread(overlay().work_area)
    if intent.name == "topic":
        if intent.topic in ("rag", "pythagoras"):
            # Hand-built lessons for these two: instant, offline, and every picture placed by hand.
            plan = rag.plan(lang, area) if intent.topic == "rag" else pythagoras.plan(lang, area)
        else:
            await _ack(speaker, lang, background)
            plan, why = await generic_plan(intent.args.get("subject") or text, lang, area)
            if plan is None:
                return Reply(_no_lesson(lang, why))
        await _run(r.start, plan, speaker, background=background)
        return Reply(plan.text(), spoken=not background)
    # From the screen. Anything already drawn goes first: the frame is read without it.
    if r.active or not r.scene.empty():
        r.clear()
        await asyncio.sleep(0.25)
    out = await screen_lesson.prepare(lang, area, want_topic=intent.topic)
    if out.lesson is None and out.material:
        # Not Pythagoras: any other subject, drawn from what is actually on the screen.
        await _ack(speaker, lang, background)
        plan, why = await generic_plan(out.material.subject, lang, area, source_text=out.material.text,
                                       intro=[out.material.intro], src=out.material.source)
        if plan is None:
            return Reply(_no_lesson(lang, why))
        await _run(r.start, plan, speaker, background=background)
        return Reply(plan.text(), spoken=not background)
    if out.lesson is None:
        return Reply(out.message)
    tracker = None
    if out.lesson.source_context.triangle_from_screen and out.page is not None:
        rect = out.lesson.extras.get("video_rect")
        tracker = screen_lesson.AnchorTracker(out.page, overlay(), area, rect)

    def run():
        if tracker:
            lesson_id = out.lesson.lesson_id
            tracker.start(lambda: r.lesson is not None and r.lesson.lesson_id == lesson_id)
        r.start(out.lesson, speaker)
    await _run(run, background=background)
    return Reply(out.lesson.text(), spoken=not background)


def _transform(r: LessonRunner, **change) -> list:
    t = dict(r.scene.transform)
    area = overlay().work_area()
    w = area["work"]
    t.setdefault("origin", [w["x"] + w["w"] / 2, w["y"] + w["h"] / 2])
    t.update(change)
    t["scale"] = max(0.4, min(3.0, t.get("scale", 1.0)))
    return [{"op": "scene.update", "transform": {"scale": round(t["scale"], 3), "dx": round(t.get("dx", 0.0), 1),
                                                   "dy": round(t.get("dy", 0.0), 1), "origin": [round(v, 1) for v in t["origin"]],
                                                   "ms": 300}}]


async def _control(c: intents.Intent, speaker: Speaker, lang: str, background: bool) -> Optional[Reply]:
    r = runner()
    ov = overlay()
    busy = r.active or not r.scene.empty()
    n = c.name
    if n == "pen_on":
        ov.control("pen-on")
        return Reply(_say("pen_on", lang))
    if n == "pen_off":
        ov.control("pen-off")
        return Reply(_say("pen_off", lang))
    if not busy and n in CLEARING and on_screen():
        ov.control("clear")
        return Reply(_say("cleared", lang))
    if not busy:
        return None
    if n == "pause":
        stopped = r.pause()
        return Reply("" if not stopped and r.status == r.PAUSED else _say("paused", lang))
    if n in ("continue", "back", "skip", "again"):
        if r.lesson is None:
            return None
        if n == "continue" and r.status != r.PAUSED:
            return Reply(_say("no_more", lang)) if r.status == r.DONE else None
        fn = {"continue": r.resume, "back": r.back, "skip": r.skip, "again": r.again}[n]
        await _run(fn, speaker, background=background)
        return Reply("", spoken=True)
    if n in ("clear", "hide"):
        r.clear()
        return Reply(_say("cleared", lang))
    if n == "clear_mine":
        r.clear("manual")
        return Reply(_say("cleared", lang))
    if n == "leave":
        r.leave()
        return Reply(_say("left", lang))
    if n == "undo":
        return Reply(_say("undone", lang) if r.undo() else _say("nothing", lang))
    if n == "redo":
        return Reply(_say("redone", lang) if r.redo() else _say("nothing", lang))
    if n in ("bigger", "smaller"):
        f = 1.2 if n == "bigger" else 1 / 1.2
        r.draw(_transform(r, scale=r.scene.transform.get("scale", 1.0) * f))
        return Reply(_say("done_drawing", lang))
    if n == "move":
        d = c.args.get("dir", "")
        dx = {"left": -140, "right": 140}.get(d, 0)
        dy = {"up": -100, "down": 100}.get(d, 0)
        r.draw(_transform(r, dx=r.scene.transform.get("dx", 0.0) + dx, dy=r.scene.transform.get("dy", 0.0) + dy))
        return Reply(_say("done_drawing", lang))
    return None


_SHAPE_N = [0]


def _target_box(r: LessonRunner) -> Optional[tuple]:
    ref = r.scene.last_ref
    return r.scene.bounds_of(ref) if ref else None


async def _draw(d: intents.Intent, lang: str) -> Reply:
    r = runner()
    area = await asyncio.to_thread(overlay().work_area)
    w = area["work"]
    cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] / 2
    k = layout.scale_for(area)
    _SHAPE_N[0] += 1
    sid = f"u{_SHAPE_N[0]}"
    style = {"color": "primary", "width": 3.5 * k, "glow": 0.6}
    a = anim("draw", 600)
    if d.name == "triangle":
        s = 160 * k
        pts = [[round(cx - s, 1), round(cy + s * 0.8, 1)], [round(cx - s, 1), round(cy - s * 0.8, 1)], [round(cx + s, 1), round(cy + s * 0.8, 1)]]
        r.draw([add(obj(sid, "triangle", points=pts, style=style, anim=a))])
    elif d.name == "circle":
        r.draw([add(obj(sid, "circle", cx=round(cx, 1), cy=round(cy, 1), r=round(120 * k, 1), style=style, anim=a))])
    elif d.name in ("rectangle",):
        r.draw([add(obj(sid, "rect", x=round(cx - 180 * k, 1), y=round(cy - 110 * k, 1), w=round(360 * k, 1), h=round(220 * k, 1),
                        r=8, style=style, anim=a))])
    elif d.name in ("arrow", "line"):
        r.draw([add(obj(sid, d.name, **{"from": [round(cx - 200 * k, 1), round(cy, 1)], "to": [round(cx + 200 * k, 1), round(cy, 1)]},
                        style=style, anim=a))])
    elif d.name in ("highlight_this", "circle_this", "label_this", "erase_that"):
        box = _target_box(r)
        if not box:
            return Reply(_say("which", lang))
        x, y, bw, bh = box
        ref = r.scene.last_ref
        if d.name == "highlight_this":
            r.draw([highlight(ref, "attention", True, 2400)], checkpoint=False)
        elif d.name == "circle_this":
            r.draw([add(obj(sid, "ellipse", cx=round(x + bw / 2, 1), cy=round(y + bh / 2, 1), rx=round(bw / 2 + 24, 1),
                            ry=round(bh / 2 + 20, 1), style={"color": "attention", "width": 3, "glow": 0.5}, anim=a))])
            r.scene.last_ref = ref
        elif d.name == "label_this":
            r.draw([add(obj(sid, "text", x=round(x + bw / 2, 1), y=round(y - 14, 1), text=d.args["label"][:60], size=round(20 * k, 1),
                            align="middle", weight="700", backing=True, anim=anim("pop", 300)))])
        else:
            r.draw([{"op": "shape.remove", "id": ref, "fade_ms": 180}])
            r.scene.last_ref = None
    elif d.name == "arrow_between":
        ids = [i for i in reversed(list(r.scene.jarvis_objects())) if r.scene.bounds_of(i)]
        if len(ids) < 2:
            return Reply(_say("arrow_which", lang))
        b1, b2 = r.scene.bounds_of(ids[1]), r.scene.bounds_of(ids[0])
        p1 = [round(b1[0] + b1[2] / 2, 1), round(b1[1] + b1[3] / 2, 1)]
        p2 = [round(b2[0] + b2[2] / 2, 1), round(b2[1] + b2[3] / 2, 1)]
        r.draw([add(obj(sid, "arrow", **{"from": p1, "to": p2}, bend=0.2, style=style, anim=a))])
    return Reply(_say("done_drawing", lang))


async def handle(text: str, speaker: Optional[Speaker] = None, background: bool = False) -> Optional[Reply]:
    """The request's effect on the overlay, or None when it is not for the overlay."""
    r = runner()
    speaker = speaker or ClockSpeaker()
    said = re.sub(r"(?i)^\s*(?:hey\s+)?jarvis[\s,.!]*", "", text or "").strip()
    lang = language_of(said)
    if r.lesson is not None and not re.search(r"(?i)\b(?:in\s+english|english\s+(?:mein|me)|hindi|hinglish)\b|हिंदी|हिन्दी", said):
        # A control keeps the lesson's language: "continue" in the middle of a Hindi lesson is not a switch to English.
        lang = r.lesson.language if intents.control(said) else lang

    le = intents.lesson(said)
    if le:
        return await _lesson(le, said, speaker, lang, background)
    c = intents.control(said)
    if c:
        rep = await _control(c, speaker, lang, background)
        if rep is not None:
            return rep
    if r.lesson is not None and (r.active or not r.scene.empty()) and r.lesson.extras.get("generic") \
            and about_the_lesson(said):
        step = await _generic_follow_up(said, r.lesson, lang)
        if step is not None:
            await _run(r.follow_up, step, speaker, background=background)
            return Reply(" ".join(p.text for p in step.phrases), spoken=not background)
    if r.lesson is not None and (r.active or not r.scene.empty()):
        fu = intents.follow_up(said, r.lesson.topic)
        if fu:
            step = (rag.follow_up(fu.name, r.lesson, lang, fu.args.get("node")) if fu.topic == "rag"
                    else _pythagoras_follow_up(fu.name, r.lesson, lang, fu.args.get("correct")))
            if step is not None:
                await _run(r.follow_up, step, speaker, background=background)
                return Reply(" ".join(p.text for p in step.phrases), spoken=not background)
    d = intents.draw(said)
    if d:
        return await _draw(d, lang)
    return None
