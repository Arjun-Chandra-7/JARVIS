"""Pythagoras, drawn while it is said — on the triangle on screen, or on a clean one.

The deterministic template: it needs no model, so it works offline, and every word of it is
known in advance, which is what lets each phrase carry exactly the picture that goes with it.

Two geometries, one script:
  * traced — the triangle was found on screen (triangle.py) with enough confidence; the sides
    are drawn over its edges and everything is attached to its anchor, so it follows a scroll.
  * standalone — a clean triangle on a panel at the side of the screen. The words then never
    suggest it came from the video.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .. import layout
from ..plan import LessonPlan, Phrase, SourceContext, Step
from ..scene import add, anim, create, highlight, obj, unhighlight, update

TOPIC = "pythagoras"


@dataclass
class FoundTriangle:
    """A right-angled triangle located on screen, in monitor logical pixels."""
    right: tuple[float, float]      # the right-angle vertex
    leg_a: tuple[float, float]      # the far end of side a
    leg_b: tuple[float, float]      # the far end of side b
    confidence: float
    anchor: Optional[dict] = None   # the validated anchor it was found against


# --------------------------------------------------------------------------- words
T = {
    "en": {
        "intro": "Let's look at the Pythagoras theorem.",
        "s1": ["Here is a right-angled triangle.", "This little square marks the right angle, ninety degrees."],
        "s2": ["The two sides that meet at the right angle are a,", "and b."],
        "s3": ["The side opposite the right angle is always the longest. It's called the hypotenuse, c."],
        "s4": ["Pythagoras says: a squared,", "plus b squared,", "equals c squared."],
        "s5": ["For example, if a is 3 and b is 4,", "then 9 plus 16 is 25,", "so c is 5."],
        "s6": ["So remember: square the two shorter sides, add them, and you get the square of the longest side."],
        "check": "Quick check: if a is 6 and b is 8, what is c?",
        "labels": ("a", "b", "c · hypotenuse"),
        "example": ("a = 3", "b = 4", "c = 5"),
        "title": "Pythagoras theorem",
    },
    "hinglish": {
        "intro": "Chaliye Pythagoras theorem samajhte hain.",
        "s1": ["Yeh ek right-angled triangle hai.", "Yeh chhota square batata hai ki yahan ninety degree ka angle hai."],
        "s2": ["Right angle pe milne wali do sides hain a,", "aur b."],
        "s3": ["Right angle ke saamne wali side hamesha sabse lambi hoti hai. Isse hypotenuse, c, kehte hain."],
        "s4": ["Pythagoras kehta hai: a square,", "plus b square,", "barabar hai c square ke."],
        "s5": ["Jaise, agar a teen hai aur b chaar,", "to nau plus solah, pachchees,", "matlab c paanch hai."],
        "s6": ["Yaad rakhiye: do chhoti sides ke square jodo, to sabse lambi side ka square milta hai."],
        "check": "Chhota sa sawaal: agar a chhe hai aur b aath, to c kitna hoga?",
        "labels": ("a", "b", "c · hypotenuse"),
        "example": ("a = 3", "b = 4", "c = 5"),
        "title": "Pythagoras theorem",
    },
    "hi": {
        "intro": "चलिए Pythagoras theorem समझते हैं।",
        "s1": ["यह एक right-angled triangle है।", "यह छोटा square बताता है कि यहाँ ninety degree का angle है।"],
        "s2": ["Right angle पर मिलने वाली दो sides हैं a,", "और b।"],
        "s3": ["Right angle के सामने वाली side हमेशा सबसे लंबी होती है। इसे hypotenuse, c, कहते हैं।"],
        "s4": ["Pythagoras कहता है: a square,", "plus b square,", "बराबर है c square के।"],
        "s5": ["जैसे, अगर a तीन है और b चार,", "तो नौ plus सोलह, पच्चीस,", "मतलब c पाँच है।"],
        "s6": ["याद रखिए: दो छोटी sides के square जोड़ो, तो सबसे लंबी side का square मिलता है।"],
        "check": "छोटा सा सवाल: अगर a छह है और b आठ, तो c कितना होगा?",
        "labels": ("a", "b", "c · hypotenuse"),
        "example": ("a = 3", "b = 4", "c = 5"),
        "title": "Pythagoras theorem",
    },
    "hi-pure": {
        "intro": "आइए पाइथागोरस प्रमेय समझते हैं।",
        "s1": ["यह एक समकोण त्रिभुज है।", "यह छोटा वर्ग बताता है कि यहाँ नब्बे अंश का कोण है।"],
        "s2": ["समकोण पर मिलने वाली दो भुजाएँ हैं लम्ब,", "और आधार।"],
        "s3": ["समकोण के सामने वाली भुजा सबसे लंबी होती है। इसे कर्ण कहते हैं।"],
        "s4": ["पाइथागोरस प्रमेय कहता है: लम्ब का वर्ग,", "जोड़ आधार का वर्ग,", "बराबर होता है कर्ण के वर्ग के।"],
        "s5": ["जैसे, अगर लम्ब तीन है और आधार चार,", "तो नौ जोड़ सोलह, पच्चीस,", "यानी कर्ण पाँच है।"],
        "s6": ["याद रखिए: दोनों छोटी भुजाओं के वर्गों का जोड़, कर्ण के वर्ग के बराबर होता है।"],
        "check": "एक छोटा प्रश्न: अगर लम्ब छह और आधार आठ है, तो कर्ण कितना होगा?",
        "labels": ("a · लम्ब", "b · आधार", "c · कर्ण"),
        "example": ("लम्ब = 3", "आधार = 4", "कर्ण = 5"),
        "title": "पाइथागोरस प्रमेय",
    },
}

GOAL = "Recognise the hypotenuse and use a² + b² = c² to find a missing side."
FOLLOW_UPS = ["explain the hypotenuse again", "why is c the longest side", "show another example",
              "go back one step", "leave it", "clear it"]


def words(language: str) -> dict:
    return T.get(language) or T["en"]


# --------------------------------------------------------------------------- geometry
def _unit(dx: float, dy: float) -> tuple[float, float]:
    d = math.hypot(dx, dy) or 1.0
    return dx / d, dy / d


def _outward(p, q, centroid, dist, size=22.0):
    """Where a side's label goes: beside the middle of side p–q, away from the triangle, and
    aligned so the text grows away from the line rather than across it.

    Returns (point, align)."""
    mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
    nx, ny = _unit(-(q[1] - p[1]), q[0] - p[0])
    if (mx + nx - centroid[0]) ** 2 + (my + ny - centroid[1]) ** 2 < (mx - centroid[0]) ** 2 + (my - centroid[1]) ** 2:
        nx, ny = -nx, -ny
    x, y = mx + nx * dist, my + ny * dist
    align = "start" if nx > 0.35 else "end" if nx < -0.35 else "middle"
    # Text hangs from its baseline: below a line it needs its own height of room.
    if ny > 0.35:
        y += size * 0.75
    elif abs(ny) <= 0.35:
        y += size * 0.35
    return [round(x, 1), round(y, 1)], align


def _geometry(area: dict, found: Optional[FoundTriangle], k: float) -> dict:
    if found:
        C, A, B = (list(map(float, found.right)), list(map(float, found.leg_a)), list(map(float, found.leg_b)))
        tri_box = layout.bbox([C, A, B])
        eq_box = layout.beside(tri_box, area, 400 * k, 190 * k)
        return {"C": C, "A": A, "B": B, "panel": None, "eq_panel": eq_box,
                "eq_at": [eq_box["x"] + eq_box["w"] / 2, eq_box["y"] + eq_box["h"] * 0.46],
                "ex_at": [eq_box["x"] + eq_box["w"] / 2, eq_box["y"] + eq_box["h"] * 0.82]}
    P = layout.panel(area, 580 * k, 680 * k)
    C = [P["x"] + P["w"] * 0.22, P["y"] + P["h"] * 0.64]
    A = [C[0], P["y"] + P["h"] * 0.17]
    B = [P["x"] + P["w"] * 0.84, C[1]]
    return {"C": C, "A": A, "B": B, "panel": P, "eq_panel": None,
            "eq_at": [P["x"] + P["w"] / 2, P["y"] + P["h"] * 0.815],
            "ex_at": [P["x"] + P["w"] / 2, P["y"] + P["h"] * 0.92]}


# --------------------------------------------------------------------------- the plan
def plan(language: str, area: dict, found: Optional[FoundTriangle] = None,
         source: Optional[SourceContext] = None, intro: Optional[list[str]] = None,
         reduced_motion: bool = False) -> LessonPlan:
    W = words(language)
    k = layout.scale_for(area)
    g = _geometry(area, found, k)
    C, A, B = g["C"], g["A"], g["B"]
    centroid = [(C[0] + A[0] + B[0]) / 3, (C[1] + A[1] + B[1]) / 3]
    anchor_id = found.anchor["id"] if found and found.anchor else None
    common = {"anchor": anchor_id} if anchor_id else {}
    width = 3.5 * k
    lab_a, lab_b, lab_c = W["labels"]
    tl = lambda n: f"step-{n}"  # noqa: E731

    def o(id, type, **kw):
        return obj(id, type, **common, **kw)

    setup: list = [create(f"pythagoras-{'traced' if found else 'standalone'}", area.get("index", 0),
                          theme={"palette": "jarvis", "glow": 0.6, "reduced_motion": reduced_motion})]
    if found and found.anchor:
        setup.append({"op": "anchor.attach", "anchor": found.anchor})
    if g["panel"]:
        P = g["panel"]
        setup.append(add(obj("py-panel", "panel", x=P["x"], y=P["y"], w=P["w"], h=P["h"], title=W["title"],
                             anim=anim("fade", 260))))
    if g["eq_panel"]:
        Q = g["eq_panel"]
        setup.append(add(o("py-eqpanel", "panel", x=Q["x"], y=Q["y"], w=Q["w"], h=Q["h"], title=W["title"],
                           anim=anim("fade", 260))))

    side = lambda id, p, q, n, delay=0: add(o(id, "line", **{"from": [round(p[0], 1), round(p[1], 1)],  # noqa: E731
                                                                   "to": [round(q[0], 1), round(q[1], 1)]},
                                              style={"color": "primary", "width": width, "glow": 0.7},
                                              anim=anim("draw", 520, delay=delay, timeline=tl(n))))
    traced = bool(found)

    def label(id, where, text, n, color="text", size=22):
        (x, y), align = where
        # Over a real page a label gets a plate; on the lesson's own panel it does not need one.
        return add(o(id, "text", x=x, y=y, text=text, size=round(size * k, 1), align=align, weight="700",
                     backing=traced, style={"color": color}, anim=anim("pop", 320, timeline=tl(n))))

    def side_label(id, p, q, text, n, color="text", far=1.0):
        return label(id, _outward(p, q, centroid, dist * far, 22 * k), text, n, color)
    terms = [{"id": "a", "text": "a", "sup": "2"}, {"id": "plus", "text": " + "},
             {"id": "b", "text": "b", "sup": "2"}, {"id": "eqs", "text": " = "},
             {"id": "c", "text": "c", "sup": "2"}]
    dist = 30 * k
    steps = [
        Step("1", "The right-angled triangle", [
            Phrase(W["s1"][0], [
                add(o("py-fill", "triangle", points=[C, A, B], style={"color": "primary", "width": 0.5, "fill": "primary",
                                                                     "fill_opacity": 0.06, "glow": 0},
                      anim=anim("fade", 500, timeline=tl(1)), z=-5)),
                side("side-a", C, A, 1), side("side-b", C, B, 1, 300), side("side-c", A, B, 1, 600)]),
            Phrase(W["s1"][1], [
                add(o("py-ra", "right_angle", at=C, a=A, b=B, size=round(24 * k, 1),
                      style={"color": "attention", "width": 2.5 * k}, anim=anim("draw", 380, timeline=tl(1)))),
                add(o("py-pulse", "pulse", x=C[0], y=C[1], r=round(16 * k, 1), repeat=2,
                      style={"color": "attention"}, anim=anim("none", 0, timeline=tl(1))))]),
        ]),
        Step("2", "The two legs", [
            Phrase(W["s2"][0], [side_label("lab-a", C, A, lab_a, 2),
                                highlight("side-a", "primary", True, 1800)]),
            Phrase(W["s2"][1], [side_label("lab-b", C, B, lab_b, 2),
                                highlight("side-b", "primary", True, 1800)]),
        ]),
        Step("3", "The hypotenuse", [
            Phrase(W["s3"][0], [highlight("side-c", "attention", True, 3200),
                                side_label("lab-c", A, B, lab_c, 3, color="attention")]),
        ]),
        Step("4", "The theorem", [
            Phrase(W["s4"][0], [add(o("eq", "equation", x=round(g["eq_at"][0], 1), y=round(g["eq_at"][1], 1),
                                      terms=terms, size=round(46 * k, 1), align="middle",
                                      anim=anim("fade", 380, timeline=tl(4)))),
                                highlight("eq#a", "primary", True, 1500), highlight("side-a", "primary", True, 1500)]),
            Phrase(W["s4"][1], [highlight("eq#b", "primary", True, 1500), highlight("side-b", "primary", True, 1500)]),
            Phrase(W["s4"][2], [highlight("eq#c", "confirm", True, 2200), highlight("side-c", "confirm", True, 2200)]),
        ]),
        Step("5", "An example", [
            # The example rewrites the side labels rather than adding numbers beside them.
            Phrase(W["s5"][0], [update("lab-a", text=W["example"][0], style={"color": "attention"}),
                                update("lab-b", text=W["example"][1], style={"color": "attention"}),
                                highlight("lab-a", "attention", True, 1400), highlight("lab-b", "attention", True, 1400)]),
            Phrase(W["s5"][1], [add(o("ex-sum", "text", x=round(g["ex_at"][0], 1), y=round(g["ex_at"][1], 1),
                                      text="3² + 4² = 9 + 16 = 25", size=round(21 * k, 1), align="middle",
                                      weight="600", font="math", style={"color": "text"},
                                      anim=anim("fade", 320, timeline=tl(5))))]),
            Phrase(W["s5"][2], [update("lab-c", text=W["example"][2], style={"color": "confirm"}),
                                highlight("lab-c", "confirm", True, 1800)]),
        ]),
        Step("6", "Remember", [
            Phrase(W["s6"][0], [unhighlight(),
                                update("eq", terms=[{**t, "color": "confirm"} if t["id"] in ("a", "b", "c") else t
                                                    for t in terms]),
                                highlight("side-c", "confirm", False)]),
            Phrase(W["check"], []),
        ]),
    ]
    opening = [Phrase(line, []) for line in (intro or [W["intro"]])]
    if opening:
        steps[0].phrases[:0] = opening
    return LessonPlan(
        topic=TOPIC, language=language if language in T else "en", learning_goal=GOAL, setup=setup, steps=steps,
        source_context=source or SourceContext(), source_confidence=found.confidence if found else 1.0,
        follow_up_options=FOLLOW_UPS, checks_for_understanding=[W["check"]],
        extras={"C": C, "A": A, "B": B, "traced": bool(found), "k": k},
    )


def answer_check(said: str) -> Optional[bool]:
    """The learner's answer to the check (6, 8 → 10), if what they said contains one."""
    import re

    s = (said or "").lower()
    if re.search(r"\b10\b|\bten\b|\bdas\b|दस|१०", s):
        return True
    if re.search(r"\b(?:\d+|fourteen|twelve|nine|eleven|chaudah|baarah)\b|चौदह|बारह", s):
        return False
    return None
