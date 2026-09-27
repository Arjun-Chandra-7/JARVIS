"""A visual lesson on any topic: the model writes the lesson as data, code draws it.

The model is asked for one JSON object — a small diagram (nodes, edges, an optional formula) and
the steps of the explanation, each saying which parts appear or are pointed at while that step is
spoken. It never produces drawing commands. This module checks every field (lengths, ids,
references, text that could be markup or a link), throws away what does not fit, lays the
diagram out deterministically in one of a few shapes, and turns it into the same kind of
``LessonPlan`` the hand-written Pythagoras and RAG lessons are.

Layouts:
  flow      a process, left to right, wrapping in a snake when long
  cycle     something that repeats (water cycle, Krebs cycle)
  tree      a hierarchy or a breakdown (classification, parts of a whole) — also any other graph
  layers    a stack (OSI model, layers of the earth)
  timeline  events in order
  compare   two things side by side

When the reply cannot be used, ``build`` returns None and the caller says so; nothing is drawn
from a half-valid lesson.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .. import layout
from ..plan import LessonPlan, Phrase, SourceContext, Step
from ..protocol import ProtocolError, VALIDATOR
from ..scene import add, anim, create, focus, highlight, obj, unfocus, unhighlight, update

LAYOUTS = ("flow", "cycle", "tree", "layers", "timeline", "compare")
ICONS = ("none", "user", "doc", "chunks", "vector", "db", "search", "context", "model", "answer", "warn", "tune")
MAX_NODES, MAX_EDGES, MAX_STEPS = 9, 12, 8

SYSTEM = (
    "You design short visual lessons for a screen overlay. The student is a CBSE Class 10 learner unless "
    "the topic is clearly beyond school; then pitch it at a curious beginner. You reply with ONE JSON "
    "object and nothing else — no prose, no markdown fences.")

SCHEMA = """{
  "title": "short title, at most 40 characters",
  "layout": "flow | cycle | tree | layers | timeline | compare",
  "columns": ["left heading", "right heading"],            // only for compare
  "nodes": [ {"id": "n1", "label": "2-3 words", "sub": "optional, at most 4 words",
              "side": "left | right",                       // only for compare
              "detail": "one plain spoken sentence explaining this part"} ],
  "edges": [ {"from": "n1", "to": "n2", "label": "optional, 1-2 words"} ],
  "formula": "optional, e.g. F = m a or E = m c^2 (use ^ for powers); omit if the topic has none",
  "steps": [ {"say": "one or two short spoken sentences", "show": ["n1"], "focus": ["n1"],
              "note": "optional caption, at most 6 words", "formula": false} ],
  "summary": "one-sentence takeaway",
  "check": "one quick question to check understanding"
}"""

RULES = """Rules:
- 3 to 8 nodes (never more than 9), 0 to 12 edges; labels short enough to fit a small box.
- 3 to 7 steps. Every node appears in some step's "show"; a node is shown before any step focuses it.
- "show" lists nodes that appear during that step; "focus" lists the ones being talked about.
- Set "formula": true on the step that introduces the formula, if there is one.
- The spoken sentences carry the explanation; labels only name things.
- Choose the layout that matches the idea's shape. Use "tree" for a breakdown or classification.
- No links, no code, no markup, no emojis."""

ACK = {"en": "Let me draw that out.", "hinglish": "Chaliye, diagram banata hoon.",
       "hi": "चलिए, diagram बनाता हूँ।", "hi-pure": "आइए, इसका चित्र बनाता हूँ।"}
TOPIC_LABEL = {"en": "", "hinglish": "", "hi": "", "hi-pure": ""}


@dataclass
class Spec:
    title: str
    layout: str
    nodes: list[dict]
    edges: list[dict]
    steps: list[dict]
    formula: str = ""
    columns: list[str] = field(default_factory=list)
    summary: str = ""
    check: str = ""


# --------------------------------------------------------------------------- prompting
def prompt(topic: str, language_instruction: str, source: str = "") -> str:
    parts = [f"Topic: {topic.strip()[:200]}"]
    if source:
        parts += ["", "What is on the student's screen right now (use it; do not claim it says anything it does not):",
                  source.strip()[:3000]]
    parts += ["", "Return JSON of exactly this shape:", SCHEMA, "", RULES, "",
              f"Language of \"say\", \"summary\", \"check\" and \"detail\": {language_instruction}",
              "Labels may stay in English where that is how a student would see them written."]
    return "\n".join(parts)


def parse_json(text: str) -> Optional[dict]:
    """The first JSON object in a model reply, tolerating fences and chatter around it."""
    if not text:
        return None
    s = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = s.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(s[start:i + 1])
                        return v if isinstance(v, dict) else None
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    return None


# --------------------------------------------------------------------------- checking
_ID = re.compile(r"[^A-Za-z0-9_.:-]")


def clean_text(v, limit: int) -> str:
    """Text the overlay will accept, or "" — never markup, links, paths or control characters."""
    if not isinstance(v, str):
        return ""
    s = re.sub(r"[\x00-\x1f\x7f‪-‮⁦-⁩]", " ", v)
    # Arrows are meaning, not markup: keep them as the characters they stand for.
    s = s.replace("<=>", "⇌").replace("<->", "↔").replace("->", "→").replace("=>", "⇒").replace("<-", "←")
    s = re.sub(r"[*_`#<>]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        cut = s[:limit].rsplit(" ", 1)[0]
        s = (cut or s[:limit]).rstrip(",;:- ") + "…"
    try:
        VALIDATOR.check("text", s, "text")
    except ProtocolError:
        return ""
    return s


def _ident(v, used: set) -> str:
    base = _ID.sub("", str(v or ""))[:20] or "n"
    ident, i = base, 2
    while ident in used:
        ident, i = f"{base}{i}", i + 1
    used.add(ident)
    return ident


def check(raw: Optional[dict]) -> Optional[Spec]:
    """A lesson that can be drawn, or None. Unusable parts are dropped, not guessed at."""
    if not isinstance(raw, dict):
        return None
    lay = raw.get("layout") if raw.get("layout") in LAYOUTS else "flow"
    used: set = set()
    idmap: dict[str, str] = {}
    nodes = []
    for n in (raw.get("nodes") or [])[:MAX_NODES]:
        if not isinstance(n, dict):
            continue
        label = clean_text(n.get("label"), 30)
        if not label:
            continue
        nid = _ident(n.get("id") or label, used)
        idmap[str(n.get("id") or label)] = nid
        nodes.append({"id": nid, "label": label, "sub": clean_text(n.get("sub"), 34),
                      "detail": clean_text(n.get("detail"), 200),
                      "side": n.get("side") if n.get("side") in ("left", "right") else "",
                      "icon": n.get("icon") if n.get("icon") in ICONS else "none"})
    if len(nodes) < 2:
        return None
    ids = {n["id"] for n in nodes}
    edges, seen = [], set()
    for e in (raw.get("edges") or [])[:MAX_EDGES * 2]:
        if not isinstance(e, dict):
            continue
        a, b = idmap.get(str(e.get("from"))), idmap.get(str(e.get("to")))
        if a in ids and b in ids and a != b and (a, b) not in seen and len(edges) < MAX_EDGES:
            seen.add((a, b))
            edges.append({"id": f"e-{a}-{b}", "from": a, "to": b, "label": clean_text(e.get("label"), 18)})
    steps = []
    for st in (raw.get("steps") or [])[:MAX_STEPS]:
        if not isinstance(st, dict):
            continue
        say = clean_text(st.get("say"), 200)
        if not say:
            continue
        pick = lambda key: [idmap[str(x)] for x in (st.get(key) or []) if str(x) in idmap][:MAX_NODES]  # noqa: E731
        steps.append({"say": say, "show": pick("show"), "focus": pick("focus"),
                      "note": clean_text(st.get("note"), 44), "formula": bool(st.get("formula"))})
    if len(steps) < 2:
        return None
    formula = clean_text(raw.get("formula"), 60) if isinstance(raw.get("formula"), str) else ""
    cols = [clean_text(c, 24) for c in (raw.get("columns") or [])[:2]] if lay == "compare" else []
    return Spec(title=clean_text(raw.get("title"), 44) or nodes[0]["label"], layout=lay, nodes=nodes, edges=edges,
                steps=steps, formula=formula, columns=[c for c in cols if c],
                summary=clean_text(raw.get("summary"), 200), check=clean_text(raw.get("check"), 160))


# --------------------------------------------------------------------------- layout
def formula_terms(f: str) -> list[dict]:
    """"E = m c^2" → terms with superscripts, one per token so each can be pointed at."""
    terms = []
    for i, tok in enumerate(re.findall(r"[^\s]+", f)[:16]):
        m = re.match(r"^(.+?)\^\(?([^)]+)\)?$", tok)
        text, sup = (m.group(1), m.group(2)) if m else (tok, "")
        sup = {"2": "2", "3": "3"}.get(sup, sup)
        term = {"id": f"f{i}", "text": (" " + text + " ") if text in ("=", "+", "-", "×", "÷", "→", "⇌", "⇒", "↔", "←") else text}
        if sup:
            term["sup"] = sup[:6]
        terms.append(term)
    return terms


def _levels(spec: Spec) -> dict[str, int]:
    """Depth of each node from the roots, for tree and layered layouts."""
    incoming = {n["id"]: 0 for n in spec.nodes}
    kids: dict[str, list[str]] = {n["id"]: [] for n in spec.nodes}
    for e in spec.edges:
        incoming[e["to"]] += 1
        kids[e["from"]].append(e["to"])
    roots = [n["id"] for n in spec.nodes if incoming[n["id"]] == 0] or [spec.nodes[0]["id"]]
    level = {r: 0 for r in roots}
    queue = list(roots)
    while queue:
        cur = queue.pop(0)
        for k in kids[cur]:
            if k not in level:
                level[k] = level[cur] + 1
                queue.append(k)
    top = max(level.values(), default=0)
    for n in spec.nodes:                       # unconnected nodes go on the last row
        level.setdefault(n["id"], top + 1 if spec.edges else 0)
    return level


def place(spec: Spec, area: dict) -> dict:
    """Panel, node boxes, and room for the formula and the caption — all in logical pixels."""
    work = area["work"]
    k = max(0.62, min(1.2, (work["w"] - 56) / 1300, (work["h"] - 60) / 820))
    nw, nh = 200 * k, 70 * k
    gx, gy = 56 * k, 84 * k
    n = len(spec.nodes)
    boxes: dict[str, tuple[float, float]] = {}      # centre of each node, relative to the diagram
    L = spec.layout
    if L == "cycle":
        R = max(150 * k, (nw + gx * 0.6) * n / (2 * math.pi))
        for i, node in enumerate(spec.nodes):
            a = -math.pi / 2 + 2 * math.pi * i / n
            boxes[node["id"]] = (R * 1.25 * math.cos(a), R * math.sin(a))
    elif L in ("tree",):
        lv = _levels(spec)
        rows: dict[int, list[str]] = {}
        for node in spec.nodes:
            rows.setdefault(lv[node["id"]], []).append(node["id"])
        for r, row in rows.items():
            width = len(row) * nw + (len(row) - 1) * gx
            for i, nid in enumerate(row):
                boxes[nid] = (-width / 2 + nw / 2 + i * (nw + gx), r * (nh + gy))
    elif L == "layers":
        nw = 460 * k
        nh = 58 * k
        for i, node in enumerate(spec.nodes):
            boxes[node["id"]] = (0.0, i * (nh + 26 * k))
    elif L == "timeline":
        span = (n - 1) * (nw * 0.72 + gx * 0.4)
        for i, node in enumerate(spec.nodes):
            boxes[node["id"]] = (-span / 2 + i * (nw * 0.72 + gx * 0.4), (-1 if i % 2 == 0 else 1) * (nh * 0.9))
    elif L == "compare":
        left = [x for x in spec.nodes if x["side"] != "right"]
        right = [x for x in spec.nodes if x["side"] == "right"]
        if not right:                                 # the model forgot the sides: split in half
            half = (n + 1) // 2
            left, right = spec.nodes[:half], spec.nodes[half:]
        for col, items in ((-1, left), (1, right)):
            for i, node in enumerate(items):
                boxes[node["id"]] = (col * (nw / 2 + gx * 1.2), (i + 0.6) * (nh + 24 * k))
    else:                                             # flow: a snake of up to four per row
        per = 4 if n > 4 else n
        for i, node in enumerate(spec.nodes):
            r, c = divmod(i, per)
            if r % 2 == 1:
                c = per - 1 - c
            boxes[node["id"]] = (c * (nw + gx), r * (nh + gy))
    xs = [x for x, _ in boxes.values()]
    ys = [y for _, y in boxes.values()]
    dw = max(xs) - min(xs) + nw
    dh = max(ys) - min(ys) + nh
    extra = (84 * k if spec.formula else 0) + 64 * k      # formula band, caption line
    head = 64 * k
    want_w = max(dw + 90 * k, 560 * k)
    want_h = dh + head + extra + 40 * k
    P = layout.panel(area, want_w, want_h, side="right" if want_w < work["w"] * 0.55 else "center")
    # Too big for the screen: scale the diagram into the panel rather than overflow it.
    s = min(1.0, (P["w"] - 60 * k) / dw, (P["h"] - head - extra - 30 * k) / dh)
    ox = P["x"] + P["w"] / 2 - ((max(xs) + min(xs)) / 2) * s
    oy = P["y"] + head + nh * s / 2 - min(ys) * s
    placed = {}
    for nid, (cx, cy) in boxes.items():
        placed[nid] = {"x": round(ox + cx * s - nw * s / 2, 1), "y": round(oy + cy * s - nh * s / 2, 1),
                       "w": round(nw * s, 1), "h": round(nh * s, 1)}
    bottom = P["y"] + head + dh * s + 16 * k
    return {"k": k, "panel": P, "boxes": placed,
            "formula_at": [round(P["x"] + P["w"] / 2, 1), round(bottom + 58 * k, 1)],
            "caption_at": [round(P["x"] + P["w"] / 2, 1), round(P["y"] + P["h"] - 22 * k, 1)],
            "axis": L == "timeline"}


def _crosses(p, q, box, pad: float = 6.0) -> bool:
    """Does segment p–q pass through ``box`` (shrunk by nothing, grown by ``pad``)?"""
    x0, y0, x1, y1 = box["x"] - pad, box["y"] - pad, box["x"] + box["w"] + pad, box["y"] + box["h"] + pad
    for i in range(1, 40):
        t = i / 40
        x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    return False


def route_around(spec: Spec, boxes: dict) -> dict[str, float]:
    """A bend for every edge whose straight line would run through another node. Seen in the
    first generated lessons: Force → Acceleration drawn straight through "Mass", striking out
    its label. Bends alternate sides so two such edges do not lie on top of each other."""
    out, side = {}, 1
    centre = lambda b: (b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)  # noqa: E731
    for e in spec.edges:
        a, b = boxes[e["from"]], boxes[e["to"]]
        p, q = centre(a), centre(b)
        if any(_crosses(p, q, box) for nid, box in boxes.items() if nid not in (e["from"], e["to"])):
            out[e["id"]] = 0.55 * side
            side = -side
    return out


# --------------------------------------------------------------------------- the plan
def build(spec: Spec, language: str, area: dict, topic: str, intro: Optional[list[str]] = None,
          source: Optional[SourceContext] = None, reduced_motion: bool = False) -> LessonPlan:
    g = place(spec, area)
    k, P, B = g["k"], g["panel"], g["boxes"]
    tl = lambda i: f"g-{i}"  # noqa: E731
    setup = [create("lesson", area.get("index", 0), theme={"palette": "jarvis", "glow": 0.6, "reduced_motion": reduced_motion}),
             add(obj("g-panel", "panel", x=P["x"], y=P["y"], w=P["w"], h=P["h"], title=spec.title, anim=anim("fade", 260)))]
    if g["axis"]:
        ys = [b["y"] + b["h"] / 2 for b in B.values()]
        mid = (min(ys) + max(ys)) / 2
        setup.append(add(obj("g-axis", "line", **{"from": [P["x"] + 30 * k, round(mid, 1)], "to": [P["x"] + P["w"] - 30 * k, round(mid, 1)]},
                             style={"color": "muted", "width": 2, "glow": 0}, anim=anim("draw", 500), z=-10)))
    if spec.layout == "compare" and spec.columns:
        xs_l = [b["x"] + b["w"] / 2 for nid, b in B.items() if next(n for n in spec.nodes if n["id"] == nid)["side"] != "right"]
        xs_r = [b["x"] + b["w"] / 2 for nid, b in B.items() if next(n for n in spec.nodes if n["id"] == nid)["side"] == "right"]
        top = min(b["y"] for b in B.values()) - 16 * k
        for i, (xs, head) in enumerate(((xs_l, spec.columns[0]), (xs_r, spec.columns[1] if len(spec.columns) > 1 else ""))):
            if xs and head:
                setup.append(add(obj(f"g-col{i}", "text", x=round(sum(xs) / len(xs), 1), y=round(top, 1), text=head,
                                     size=round(18 * k, 1), align="middle", weight="700",
                                     style={"color": "confirm" if i == 0 else "attention"}, anim=anim("fade", 260))))
    by_id = {n["id"]: n for n in spec.nodes}
    bends = route_around(spec, B)
    shown: set = set()
    drawn_edges: set = set()
    flowing: list = []
    steps: list[Step] = []
    formula_shown = False
    terms = formula_terms(spec.formula) if spec.formula else []

    def node_cmd(nid, t):
        n, b = by_id[nid], B[nid]
        o = obj(nid, "node", x=b["x"], y=b["y"], w=b["w"], h=b["h"], label=n["label"], icon=n["icon"],
                anim=anim("pop", 340, timeline=t))
        if n["sub"]:
            o["sub"] = n["sub"]
        return add(o)

    for i, st in enumerate(spec.steps):
        t = tl(i + 1)
        visuals: list = [update(e, flow=False) for e in flowing]
        flowing = []
        new = [nid for nid in st["show"] + st["focus"] if nid not in shown]
        if i == len(spec.steps) - 1:
            new += [n["id"] for n in spec.nodes if n["id"] not in shown and n["id"] not in new]
        for nid in dict.fromkeys(new):
            visuals.append(node_cmd(nid, t))
            shown.add(nid)
        for e in spec.edges:
            if e["id"] not in drawn_edges and e["from"] in shown and e["to"] in shown:
                o = obj(e["id"], "edge", **{"from": e["from"], "to": e["to"]}, flow=True,
                        bend=bends.get(e["id"], 0.25 if spec.layout == "cycle" else 0),
                        anim=anim("draw", 480, delay=150, timeline=t))
                if e["label"]:
                    o["label"] = e["label"]
                visuals.append(add(o))
                drawn_edges.add(e["id"])
                flowing.append(e["id"])
        if (st["formula"] or (i == len(spec.steps) - 1)) and terms and not formula_shown:
            visuals.append(add(obj("g-formula", "equation", x=g["formula_at"][0], y=g["formula_at"][1], terms=terms,
                                   size=round(40 * k, 1), align="middle", anim=anim("fade", 380, timeline=t))))
            visuals.append(highlight("g-formula", "confirm", True, 1800))
            formula_shown = True
        visuals.append(unhighlight())
        for nid in st["focus"]:
            visuals.append(highlight(nid, "attention", True, 2600))
        if st["note"]:
            cap = obj("g-caption", "text", x=g["caption_at"][0], y=g["caption_at"][1], text=st["note"],
                      size=round(16 * k, 1), align="middle", weight="600", style={"color": "muted"}, anim=anim("fade", 240, timeline=t))
            visuals.append(add(cap))
        steps.append(Step(str(i + 1), st["note"] or f"Step {i + 1}", [Phrase(st["say"], visuals)]))
    closing = [Phrase(spec.summary, [update(e, flow=False) for e in flowing] + [unhighlight(), unfocus()])] if spec.summary else []
    if spec.check:
        closing.append(Phrase(spec.check, []))
    if closing:
        steps.append(Step(str(len(steps) + 1), "Summary", closing))
    if intro:
        steps[0].phrases[:0] = [Phrase(line, []) for line in intro]
    return LessonPlan(topic=topic[:80] or spec.title, language=language, learning_goal=spec.summary or spec.title,
                      setup=setup, steps=steps, source_context=source or SourceContext(),
                      follow_up_options=["explain <part> again", "go back one step", "give an example", "clear it"],
                      checks_for_understanding=[spec.check] if spec.check else [],
                      cleanup_policy={"auto_clear_s": 20.0, "keep": False},
                      extras={"generic": True, "spec": spec, "geometry": g})


def node_named(spec: Spec, text: str) -> Optional[str]:
    """The node the person means: the one whose label shares the most words with what they said."""
    words = set(re.findall(r"\w{3,}", (text or "").lower()))
    best, score = None, 0
    for n in spec.nodes:
        label = set(re.findall(r"\w{3,}", (n["label"] + " " + n["sub"]).lower()))
        s = len(words & label)
        if s > score:
            best, score = n["id"], s
    return best


def explain_again(lesson: LessonPlan, text: str) -> Optional[Step]:
    """"Explain the X again": that part, focused, with its own sentence — no model needed."""
    spec: Spec = lesson.extras["spec"]
    nid = node_named(spec, text)
    if not nid:
        return None
    n = next(x for x in spec.nodes if x["id"] == nid)
    say = n["detail"] or next((s["say"] for s in spec.steps if nid in s["focus"] or nid in s["show"]), "")
    if not say:
        return None
    edges = [e["id"] for e in spec.edges if nid in (e["from"], e["to"])]
    return Step(f"fu-{nid}", n["label"], [Phrase(say, [unhighlight(), focus(nid, *edges), highlight(nid, "attention", True, 3000)])])


FOLLOW_SYSTEM = (
    "You are continuing a visual lesson that is on the student's screen. Reply with ONE JSON object and nothing "
    "else: {\"say\": \"one to three short spoken sentences answering them\", \"focus\": [ids of the diagram parts "
    "you talk about], \"note\": \"optional caption, at most 6 words\"}. Use only ids from the diagram. No markdown.")


def follow_up_prompt(lesson: LessonPlan, question: str, language_instruction: str) -> str:
    spec: Spec = lesson.extras["spec"]
    parts = "\n".join(f"- {n['id']}: {n['label']}" + (f" ({n['sub']})" if n["sub"] else "") for n in spec.nodes)
    said = " ".join(lesson.spoken_segments)[:1500]
    return (f"Lesson: {spec.title}\nDiagram parts:\n{parts}\n\nWhat you already said: {said}\n\n"
            f"The student now says: \"{question.strip()[:300]}\"\nAnswer that, adding something new rather than repeating. "
            f"{language_instruction}")


def follow_up_step(lesson: LessonPlan, reply: str) -> Optional[Step]:
    raw = parse_json(reply)
    spec: Spec = lesson.extras["spec"]
    if not raw:
        return None
    say = clean_text(raw.get("say"), 400)
    if not say:
        return None
    ids = {n["id"] for n in spec.nodes}
    targets = [str(x) for x in (raw.get("focus") or []) if str(x) in ids][:6]
    visuals: list = [unhighlight(), unfocus()]
    if targets:
        visuals += [focus(*targets)] + [highlight(t, "attention", True, 2600) for t in targets]
    note = clean_text(raw.get("note"), 44)
    if note:
        g = lesson.extras["geometry"]
        visuals.append(add(obj("g-caption", "text", x=g["caption_at"][0], y=g["caption_at"][1], text=note,
                               size=round(16 * g["k"], 1), align="middle", weight="600", style={"color": "muted"},
                               anim=anim("fade", 240))))
    return Step("fu-ask", "Follow-up", [Phrase(say, visuals)])
