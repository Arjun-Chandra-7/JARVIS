// The teaching layer's renderer: validated commands in, crisp vector drawing out.
//
// Diagram objects are SVG — resolution-independent, so a label is as sharp at 150 % scaling as
// at 100 %, and every object is its own element that can be highlighted, dimmed or moved without
// repainting the rest. Manual ink is a canvas sized in physical pixels, because a pen stroke is
// thousands of points and redrawn as one bitmap.
//
// Nothing here runs while nothing moves: the animation loop exists only while an animation or a
// pulse is in flight, and stops itself. A static scene costs no frames at all.
//
// Commands arrive already validated by the main process, and are validated again here — this
// file also assembles objects from patches, and a merged object is checked as a whole before it
// is drawn. Text is only ever set with textContent.
"use strict";

(async function () {
  const G = window.TeachGeometry;
  const NS = "http://www.w3.org/2000/svg";
  const api = window.teach;
  const spec = await api.spec();
  const V = window.TeachProtocol.make(spec);
  const LIMITS = spec.limits;

  const body = document.body;
  const stage = document.getElementById("stage");
  const world = document.getElementById("world");
  const ink = document.getElementById("ink");
  const penBar = document.getElementById("pen-bar");
  const epoch = (t) => performance.timeOrigin + t;
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  const S = {
    gen: -1,
    lesson: "",
    scene: "",
    objects: new Map(),       // id → { obj, root, parts, rect }
    groups: new Map(),        // id → <g>
    anchors: new Map(),       // id → { a, base }
    anims: new Set(),
    pausedAll: false,
    paused: new Set(),
    pending: [],              // scheduled batches: { timer, gen, lesson }
    highlights: new Map(),    // ref → timer
    theme: { palette: "jarvis", glow: 0.6, text_scale: 1, reduced_motion: false },
    transform: { scale: 1, dx: 0, dy: 0, origin: [0, 0] },
    geometry: { offset: { x: 0, y: 0 }, w: window.innerWidth, h: window.innerHeight },
    focus: null,
    lowq: false,
    frameTimes: [],
  };

  const reduced = () => S.theme.reduced_motion || reducedQuery.matches;

  // ---------------------------------------------------------------- helpers
  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) node.setAttribute(k, String(v));
    if (parent) parent.appendChild(node);
    return node;
  }

  // Palette names become CSS variables, so a palette switch recolours everything at once.
  function paint(c, fallback) {
    const v = c || fallback;
    if (!v || v === "none") return "none";
    return v.startsWith("#") ? v : `var(--c-${v})`;
  }

  function textSize(n, dflt) {
    return (n || dflt) * (S.theme.text_scale || 1);
  }

  function report(evt) {
    try { api.report(evt); } catch { /* the main process is going away */ }
  }

  function group(id) {
    const gid = id || "jarvis";
    let g = S.groups.get(gid);
    if (!g) {
      if (S.groups.size >= LIMITS.max_groups) throw new Error("too many groups");
      g = el("g", { "data-group": gid }, world);
      S.groups.set(gid, g);
    }
    return g;
  }

  // SVG has no z-index; order within a group is document order, kept sorted by z.
  function insertByZ(layer, root, z) {
    root.dataset.z = String(z || 0);
    for (const sib of layer.children) {
      if (Number(sib.dataset.z || 0) > (z || 0)) {
        layer.insertBefore(root, sib);
        return;
      }
    }
    layer.appendChild(root);
  }

  // A stroked path with its glow: a wider, fainter, blurred copy underneath.
  function stroked(parent, tag, attrs, style, dflt, parts) {
    const st = { ...dflt, ...(style || {}) };
    const glow = st.glow === undefined ? 1 : st.glow;
    let halo = null;
    if (glow > 0 && S.theme.palette !== "contrast") {
      halo = el(tag, attrs, parent);
      halo.classList.add("halo");
      halo.style.fill = "none";
      halo.style.stroke = paint(st.color, "primary");
      halo.style.strokeWidth = String((st.width || 3) + 7 * glow);
      halo.style.strokeLinecap = "round";
      halo.style.strokeLinejoin = "round";
    }
    const core = el(tag, attrs, parent);
    core.classList.add("core");
    core.style.fill = paint(st.fill, "none");
    if (st.fill && st.fill !== "none") core.style.fillOpacity = String(st.fill_opacity === undefined ? 0.12 : st.fill_opacity);
    core.style.stroke = paint(st.color, "primary");
    core.style.strokeWidth = String(st.width || 3);
    core.style.strokeLinecap = "round";
    core.style.strokeLinejoin = "round";
    if (st.opacity !== undefined) core.style.opacity = String(st.opacity);
    if (st.dash === "dashed") core.style.strokeDasharray = `${(st.width || 3) * 3} ${(st.width || 3) * 2.5}`;
    if (st.dash === "dotted") core.style.strokeDasharray = `0.1 ${(st.width || 3) * 2.4}`;
    parts.push({ el: core, kind: "stroke", dashed: !!st.dash && st.dash !== "solid" });
    if (halo) parts.push({ el: halo, kind: "stroke", halo: true });
    return core;
  }

  function label(parent, x, y, text, opts, parts) {
    const o = opts || {};
    const t = el("text", {
      x, y,
      "text-anchor": o.align || "start",
      "dominant-baseline": o.baseline || "alphabetic",
      "font-size": textSize(o.size, 18),
      "font-weight": o.weight || 500,
    }, parent);
    if (o.cls) t.classList.add(...o.cls.split(" "));
    t.style.fill = paint(o.color, "text");
    t.textContent = text;
    parts.push({ el: t, kind: "fill" });
    return t;
  }

  // A plate behind text, sized to the text as laid out — so it fits Devanagari and Latin alike.
  function backing(parent, target, padX, padY, parts, radius) {
    let box;
    try { box = target.getBBox(); } catch { return null; }
    if (!box || !box.width) return null;
    const r = el("rect", {
      x: box.x - padX, y: box.y - padY, width: box.width + 2 * padX, height: box.height + 2 * padY,
      rx: radius === undefined ? 7 : radius,
    });
    r.classList.add("backing");
    // Directly under its own text, so it covers whatever line the text sits on.
    parent.insertBefore(r, target);
    parts.push({ el: r, kind: "fill" });
    return r;
  }

  // Shrink a label until it fits its box. Measured, because Devanagari and Latin at the same size
  // are different widths and a fixed character budget would be wrong for one of them.
  function fit(t, maxW, minSize) {
    let size = Number(t.getAttribute("font-size"));
    let w = 0;
    try { w = t.getComputedTextLength(); } catch { return; }
    if (!w || w <= maxW) return;
    size = Math.max(minSize || 9, Math.floor(size * maxW / w * 10) / 10);
    t.setAttribute("font-size", String(size));
  }

  // ---------------------------------------------------------------- icons (built-in, never data)
  const ICONS = {
    user: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"],
    doc: ["M6 3h8l4 4v14H6z", "M14 3v4h4", "M9 12h6", "M9 16h6"],
    chunks: ["M4 4h7v7H4z", "M13 4h7v7h-7z", "M4 13h7v7H4z", "M13 13h7v7h-7z"],
    vector: ["M4 20L20 4", "M20 4h-6", "M20 4v6", "M4 20l5-11", "M4 20l11-5"],
    db: ["M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z", "M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6", "M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"],
    search: ["M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13z", "M15.5 15.5L21 21"],
    context: ["M4 5h16v14H4z", "M8 9h8", "M8 13h5"],
    model: ["M8 3h8v4H8z", "M5 7h14v12H5z", "M9 12h.01", "M15 12h.01", "M9 16h6"],
    answer: ["M4 5h16v11H9l-5 4z", "M8 10l2.5 2.5L16 8"],
    warn: ["M12 3l10 18H2z", "M12 10v5", "M12 18h.01"],
    tune: ["M4 7h10", "M18 7h2", "M16 5v4", "M4 17h4", "M12 17h8", "M10 15v4"],
  };

  function icon(parent, name, x, y, size, color, parts) {
    const paths = ICONS[name];
    if (!paths) return;
    const g = el("g", { transform: `translate(${x} ${y}) scale(${size / 24})` }, parent);
    for (const d of paths) {
      const p = el("path", { d }, g);
      p.classList.add("node-icon");
      p.style.stroke = paint(color, "primary");
    }
    parts.push({ el: g, kind: "fill" });
  }

  // ---------------------------------------------------------------- building objects
  function rectOf(obj) {
    if ("w" in obj && "h" in obj && "x" in obj) return { x: obj.x, y: obj.y, w: obj.w, h: obj.h };
    if (obj.type === "circle") return { x: obj.cx - obj.r, y: obj.cy - obj.r, w: 2 * obj.r, h: 2 * obj.r };
    if (obj.type === "ellipse") return { x: obj.cx - obj.rx, y: obj.cy - obj.ry, w: 2 * obj.rx, h: 2 * obj.ry };
    return null;
  }

  const BUILD = {
    "stroke": (o, g, parts) => stroked(g, "path", { d: G.smoothPath(o.points) }, o.style, { color: "primary", width: 3 }, parts),
    "highlighter": (o, g, parts) => {
      const p = stroked(g, "path", { d: G.smoothPath(o.points) }, { glow: 0, ...o.style },
                        { color: "attention", width: 16, opacity: 0.38 }, parts);
      p.style.strokeLinecap = "round";
    },
    "line": (o, g, parts) => stroked(g, "path", { d: G.curve(o.from, o.to, 0).d }, o.style, { color: "primary", width: 3 }, parts),
    "arrow": (o, g, parts) => {
      const c = G.curve(o.from, o.to, o.bend || 0);
      const head = G.arrowHead(o.to, c.tangent, o.head || 14);
      stroked(g, "path", { d: G.curve(o.from, head.shaftEnd, o.bend || 0).d }, o.style, { color: "primary", width: 3 }, parts);
      const h = el("path", { d: head.d }, g);
      h.style.fill = paint((o.style || {}).color, "primary");
      parts.push({ el: h, kind: "fill", late: true });
    },
    "rect": (o, g, parts) => stroked(g, "rect", { x: o.x, y: o.y, width: o.w, height: o.h, rx: o.r || 0 }, o.style, { color: "primary", width: 2.5 }, parts),
    "circle": (o, g, parts) => stroked(g, "circle", { cx: o.cx, cy: o.cy, r: o.r }, o.style, { color: "primary", width: 3 }, parts),
    "ellipse": (o, g, parts) => stroked(g, "ellipse", { cx: o.cx, cy: o.cy, rx: o.rx, ry: o.ry }, o.style, { color: "primary", width: 3 }, parts),
    "triangle": (o, g, parts) => stroked(g, "path", { d: G.polygonPath(o.points) }, o.style, { color: "primary", width: 3.5 }, parts),
    "right_angle": (o, g, parts) => stroked(g, "path", { d: G.rightAngle(o.at, o.a, o.b, o.size || 22) }, o.style,
                                            { color: "attention", width: 2.5, glow: 0.4 }, parts),
    "text": (o, g, parts) => {
      const t = label(g, o.x, o.y, o.text, {
        size: o.size || 20, align: o.align, weight: o.weight || 600, color: (o.style || {}).color || "text",
        cls: o.font === "math" ? "math" : o.font === "mono" ? "mono" : "",
      }, parts);
      if (o.backing) {
        t.classList.add("plain");
        backing(g, t, 10, 6, parts);
      }
    },
    "equation": (o, g, parts) => {
      const size = textSize(o.size, 40);
      const t = el("text", { x: o.x, y: o.y, "text-anchor": o.align || "start", "font-size": size }, g);
      t.classList.add("math");
      let raised = false;
      for (const term of o.terms) {
        const span = el("tspan", {}, t);
        span.dataset.term = term.id;
        span.style.fill = paint(term.color || (o.style || {}).color, "text");
        const base = el("tspan", raised ? { dy: size * 0.4 } : {}, span);
        base.textContent = term.text;
        raised = false;
        if (term.sup) {
          const sup = el("tspan", { dy: -size * 0.4, "font-size": size * 0.6 }, span);
          sup.textContent = term.sup;
          raised = true;
        }
      }
      parts.push({ el: t, kind: "fill" });
      if (o.backing) {
        t.classList.add("plain");
        backing(g, t, 16, 10, parts, 10);
      }
    },
    "node": (o, g, parts) => {
      const st = o.style || {};
      stroked(g, "rect", { x: o.x, y: o.y, width: o.w, height: o.h, rx: 12 }, { glow: 0.5, ...st, fill: "backing", fill_opacity: 0.82 },
              { color: "primary", width: 1.8 }, parts);
      const withIcon = o.icon && o.icon !== "none";
      const pad = withIcon ? 50 : 0;
      const cx = withIcon ? o.x + pad : o.x + o.w / 2;
      const anchor = withIcon ? "start" : "middle";
      if (withIcon) icon(g, o.icon, o.x + 14, o.y + o.h / 2 - 12, 24, st.color || "primary", parts);
      const size = Math.min(18, Math.max(12, o.h / 3.6));
      const room = o.w - (withIcon ? pad + 12 : 24);
      // One line if it fits at a readable size; otherwise the title takes two lines and the
      // subtitle gives way — a clipped title is worse than a missing subtitle.
      const probe = label(g, cx, o.y + o.h / 2, o.label, { size, weight: 600, align: anchor, baseline: "central", cls: "node-label plain" }, parts);
      let width = 0;
      try { width = probe.getComputedTextLength(); } catch { width = 0; }
      const words = o.label.split(" ");
      if (width > room * 1.1 && words.length > 1) {
        probe.remove();
        parts.pop();
        const cut = Math.ceil(words.length / 2);
        const lines = [words.slice(0, cut).join(" "), words.slice(cut).join(" ")];
        lines.forEach((line, i) => fit(label(g, cx, o.y + o.h / 2 + (i - 0.5) * size * 1.12, line,
          { size, weight: 600, align: anchor, baseline: "central", cls: "node-label plain" }, parts), room, 10));
      } else if (o.sub) {
        probe.setAttribute("y", String(o.y + o.h / 2 - 3));
        probe.setAttribute("dominant-baseline", "alphabetic");
        fit(probe, room, 11);
        fit(label(g, cx, o.y + o.h / 2 + size * 0.95, o.sub, { size: size * 0.74, weight: 500, align: anchor, color: "muted", cls: "node-sub plain" }, parts), room, 9);
      } else {
        fit(probe, room, 11);
      }
    },
    "edge": (o, g, parts, rec) => {
      const a = S.objects.get(o.from);
      const b = S.objects.get(o.to);
      const ra = a && rectOf(a.obj);
      const rb = b && rectOf(b.obj);
      rec.pending = !(ra && rb);
      if (rec.pending) return;          // drawn once both ends exist
      const ends = G.edgeBetween(ra, rb, 6);
      const c = G.curve(ends.from, ends.to, o.bend || 0);
      const head = G.arrowHead(ends.to, c.tangent, 11);
      const shaft = stroked(g, "path", { d: G.curve(ends.from, head.shaftEnd, o.bend || 0).d }, { glow: 0.4, ...o.style },
                            { color: "muted", width: 2 }, parts);
      const h = el("path", { d: head.d }, g);
      h.style.fill = paint((o.style || {}).color, "muted");
      parts.push({ el: h, kind: "fill", late: true });
      if (o.flow && !reduced()) {
        const f = el("path", { d: shaft.getAttribute("d") }, g);
        f.classList.add("flowing");
        f.style.fill = "none";
        f.style.stroke = "var(--c-primary)";
        f.style.strokeWidth = "3";
        f.style.strokeLinecap = "round";
        parts.push({ el: f, kind: "fill", late: true });
      }
      if (o.label) {
        // Sized with the nodes it joins, and left out when the edge is shorter than its words:
        // a label spilling onto the boxes either side reads as part of them.
        const lsize = Math.max(10, Math.min(13, Math.min(ra.h, rb.h) / 5.8));
        const t = label(g, c.mid[0], c.mid[1] - 8, o.label, { size: lsize, weight: 500, align: "middle", color: "muted", cls: "plain" }, parts);
        let tw = 0;
        try { tw = t.getComputedTextLength(); } catch { tw = 0; }
        const span = Math.hypot(ends.to[0] - ends.from[0], ends.to[1] - ends.from[1]);
        const across = Math.abs(ends.to[0] - ends.from[0]) > Math.abs(ends.to[1] - ends.from[1]);
        if (across && tw + 12 > span) {
          t.remove();
          parts.pop();
        } else {
          backing(g, t, 6, 3, parts, 5);
        }
      }
    },
    "region": (o, g, parts) => {
      const pad = o.pad === undefined ? 6 : o.pad;
      const st = o.style || {};
      const r = stroked(g, "rect", { x: o.x - pad, y: o.y - pad, width: o.w + 2 * pad, height: o.h + 2 * pad, rx: 8 },
                        { ...st, fill: st.fill || st.color || "primary", fill_opacity: st.fill_opacity === undefined ? 0.07 : st.fill_opacity },
                        { color: "primary", width: 2.5 }, parts);
      r.classList.add("region-fill");
      if (o.label) {
        const t = label(g, o.x - pad + 10, o.y - pad - 10, o.label, { size: 14, weight: 600, color: st.color || "primary", cls: "tag-text plain" }, parts);
        backing(g, t, 8, 4, parts, 6);
      }
    },
    "spotlight": (o, g, parts) => {
      const big = 20000;
      const r = o.round ? Math.min(o.w, o.h) / 2 : 12;
      const hole = `M${o.x + r},${o.y}H${o.x + o.w - r}A${r},${r} 0 0 1 ${o.x + o.w},${o.y + r}V${o.y + o.h - r}` +
                   `A${r},${r} 0 0 1 ${o.x + o.w - r},${o.y + o.h}H${o.x + r}A${r},${r} 0 0 1 ${o.x},${o.y + o.h - r}` +
                   `V${o.y + r}A${r},${r} 0 0 1 ${o.x + r},${o.y}Z`;
      const p = el("path", { d: `M${-big},${-big}H${big}V${big}H${-big}Z` + hole, "fill-rule": "evenodd" }, g);
      p.style.fill = "#000";
      p.style.fillOpacity = String(o.dim === undefined ? 0.5 : o.dim);
      parts.push({ el: p, kind: "fill" });
    },
    "pulse": (o, g, parts, rec) => {
      const c = el("circle", { cx: o.x, cy: o.y, r: o.r || 18 }, g);
      c.style.fill = "none";
      c.style.stroke = paint((o.style || {}).color, "primary");
      c.style.strokeWidth = "3";
      parts.push({ el: c, kind: "pulse" });
      rec.pulse = { el: c, r: o.r || 18, repeat: o.repeat || 3 };
    },
    "panel": (o, g, parts) => {
      const r = el("rect", { x: o.x, y: o.y, width: o.w, height: o.h, rx: 16 }, g);
      r.classList.add("panel-frame");
      parts.push({ el: r, kind: "fill" });
      if (o.title) {
        // Small capitals suit Latin titles; spacing Devanagari apart breaks its conjuncts.
        const latin = /^[\x20-\x7e]+$/.test(o.title);
        label(g, o.x + 22, o.y + 34, latin ? o.title.toUpperCase() : o.title,
              { size: 13, weight: 600, color: "muted", cls: `panel-title plain${latin ? " spaced" : ""}` }, parts);
      }
    },
  };

  function build(obj, animate) {
    const layer = group(obj.group);
    const root = el("g", { "data-id": obj.id });
    root.classList.add("obj");
    const inner = el("g", {}, root);
    insertByZ(layer, root, obj.z !== undefined ? obj.z : obj.type === "spotlight" ? -50 : obj.type === "panel" ? -40 : 0);
    const parts = [];
    const rec = { obj, root, inner, parts, rect: rectOf(obj) };
    BUILD[obj.type](obj, inner, parts, rec);
    if (obj.visible === false) root.classList.add("hidden");
    if (obj.anchor) placeAnchored(rec);
    if (S.focus && !S.focus.has(obj.id) && obj.type !== "panel") root.classList.add("dimmed");
    if (animate && obj.anim && obj.anim.kind !== "none" && !reduced()) startAnim(rec, obj.anim);
    else if (rec.pulse && !reduced()) startAnim(rec, { kind: "none", ms: 0 });
    return rec;
  }

  function add(obj, animate) {
    V.object(obj, "object");
    const existing = S.objects.get(obj.id);
    if (!existing && S.objects.size >= LIMITS.max_objects) throw new Error("too many objects");
    if (existing) destroy(obj.id);
    const rec = build(obj, animate);
    S.objects.set(obj.id, rec);
    // Edges waiting for this node, and edges already drawn to it, are laid out again.
    if (obj.type !== "edge") relayoutEdges(obj.id);
    return rec;
  }

  function relayoutEdges(nodeId) {
    for (const [id, rec] of S.objects) {
      if (rec.obj.type === "edge" && (rec.obj.from === nodeId || rec.obj.to === nodeId)) {
        const wasPending = rec.pending;
        const r = replace(id, rec.obj);
        if (wasPending && r && rec.obj.anim) startAnim(r, rec.obj.anim);
      }
    }
  }

  // Rebuilt in place, at the same depth, without animating again.
  function replace(id, obj) {
    const old = S.objects.get(id);
    if (!old) return null;
    const next = old.root.nextSibling;
    const parent = old.root.parentNode;
    stopAnimsOf(old);
    old.root.remove();
    const rec = build(obj, false);
    if (parent && rec.root.parentNode === parent && next && next.parentNode === parent) parent.insertBefore(rec.root, next);
    S.objects.set(id, rec);
    return rec;
  }

  function destroy(id) {
    const rec = S.objects.get(id);
    if (!rec) return;
    stopAnimsOf(rec);
    rec.root.remove();
    S.objects.delete(id);
    const t = S.highlights.get(id);
    if (t) clearTimeout(t);
    S.highlights.delete(id);
  }

  function remove(id, fadeMs) {
    const rec = S.objects.get(id);
    if (!rec) return;
    const ms = reduced() ? 0 : fadeMs === undefined ? 180 : fadeMs;
    if (!ms) {
      destroy(id);
      return;
    }
    S.objects.delete(id);           // gone from the scene now; only its fade is left on screen
    stopAnimsOf(rec);
    addAnim({ rec, t0: performance.now(), dur: ms, ease: G.EASE.linear, timeline: "",
              apply: (p) => { rec.root.style.opacity = String(1 - p); },
              done: () => rec.root.remove() });
  }

  // ---------------------------------------------------------------- anchors
  function placeAnchored(rec) {
    const entry = S.anchors.get(rec.obj.anchor);
    if (!entry) {
      rec.root.classList.add("hidden");
      return;
    }
    rec.root.setAttribute("transform", G.anchorTransform(entry.base, entry.a.bounds));
    rec.root.classList.toggle("hidden", !anchorLive(entry) || rec.obj.visible === false);
  }

  function anchorLive(entry) {
    const a = entry.a;
    if (a.confidence < 0.5) return false;
    if (a.tracking !== "static" && Date.now() > a.observed_at + a.ttl_ms) return false;
    return true;
  }

  let anchorTimer = null;
  function watchAnchors() {
    if (anchorTimer || S.anchors.size === 0) return;
    anchorTimer = setInterval(() => {
      if (S.anchors.size === 0) {
        clearInterval(anchorTimer);
        anchorTimer = null;
        return;
      }
      for (const rec of S.objects.values()) if (rec.obj.anchor) placeAnchored(rec);
    }, 200);
  }

  // ---------------------------------------------------------------- animation
  function addAnim(a) {
    S.anims.add(a);
    a.rec.anims = a.rec.anims || new Set();
    a.rec.anims.add(a);
    loop();
  }

  function stopAnimsOf(rec) {
    if (!rec.anims) return;
    for (const a of rec.anims) S.anims.delete(a);
    rec.anims.clear();
  }

  function prepareDraw(parts) {
    for (const p of parts) {
      if (p.kind === "stroke" && !p.dashed && p.el.getTotalLength) {
        let len = 0;
        try { len = p.el.getTotalLength(); } catch { len = 0; }
        p.len = len || 1;
        p.el.style.strokeDasharray = `${p.len} ${p.len}`;
        p.el.style.strokeDashoffset = String(p.len);
      } else {
        p.el.style.opacity = "0";
      }
    }
  }

  function startAnim(rec, anim) {
    const kind = anim.kind;
    const dur = Math.min(anim.ms || 0, LIMITS.max_anim_ms);
    const ease = G.EASE[anim.ease || "out"] || G.EASE.out;
    const t0 = performance.now() + (anim.delay || 0);
    const timeline = anim.timeline || "";
    const inner = rec.inner;
    if (kind === "draw" || kind === "flow") {
      prepareDraw(rec.parts);
      addAnim({ rec, t0, dur: Math.max(dur, 1), ease, timeline,
        apply: (p) => {
          for (const part of rec.parts) {
            if (part.len !== undefined) part.el.style.strokeDashoffset = String(part.len * (1 - p));
            else if (part.late) part.el.style.opacity = String(Math.max(0, (p - 0.8) / 0.2));
            else part.el.style.opacity = String(Math.min(1, p * 1.6));
          }
        },
        done: () => {
          for (const part of rec.parts) {
            if (part.len !== undefined) {
              part.el.style.strokeDasharray = "";
              part.el.style.strokeDashoffset = "";
              delete part.len;
            }
            part.el.style.opacity = "";
          }
        } });
    } else if (kind === "fade") {
      inner.style.opacity = "0";
      addAnim({ rec, t0, dur: Math.max(dur, 1), ease, timeline,
        apply: (p) => { inner.style.opacity = String(p); }, done: () => { inner.style.opacity = ""; } });
    } else if (kind === "pop" || kind === "grow") {
      const from = kind === "pop" ? 0.86 : 0.05;
      inner.style.transformBox = "fill-box";
      inner.style.transformOrigin = "center";
      inner.style.opacity = "0";
      addAnim({ rec, t0, dur: Math.max(dur, 1), ease, timeline,
        apply: (p) => {
          inner.style.opacity = String(Math.min(1, p * 1.5));
          inner.style.transform = `scale(${from + (1 - from) * p})`;
        },
        done: () => { inner.style.opacity = ""; inner.style.transform = ""; } });
    }
    if (rec.pulse) {
      const { el: c, r, repeat } = rec.pulse;
      addAnim({ rec, t0, dur: 900 * repeat, ease: G.EASE.linear, timeline,
        apply: (p) => {
          const q = (p * repeat) % 1;
          c.setAttribute("r", String(r * (1 + 1.4 * q)));
          c.style.opacity = String(1 - q);
        },
        done: () => { c.style.opacity = "0"; } });
    }
  }

  let raf = 0;
  let lastFrame = 0;
  let run = null;                 // frame statistics for the current burst of animation
  function loop() {
    if (raf || S.anims.size === 0) return;
    lastFrame = performance.now();
    if (!run) run = { frames: 0, total: 0, max: 0 };
    raf = requestAnimationFrame(tick);
  }

  // When the animation stops, say how smooth it was: frames, mean and worst frame time.
  function endRun() {
    if (run && run.frames >= 5) {
      report({ type: "anim", frames: run.frames, avg_frame_ms: Math.round(run.total / run.frames * 10) / 10,
               max_frame_ms: Math.round(run.max * 10) / 10 });
    }
    run = null;
  }

  function tick(now) {
    raf = 0;
    const dt = Math.max(0, now - lastFrame);
    lastFrame = now;
    measure(dt);
    if (run && dt > 0 && dt < 250) {
      run.frames++;
      run.total += dt;
      run.max = Math.max(run.max, dt);
    }
    for (const a of Array.from(S.anims)) {
      if (S.pausedAll || (a.timeline && S.paused.has(a.timeline))) {
        a.t0 += dt;                   // a paused animation holds its progress
        continue;
      }
      const p = a.dur ? Math.min(1, Math.max(0, (now - a.t0) / a.dur)) : 1;
      if (now < a.t0) continue;
      a.apply(a.ease(p));
      if (p >= 1) {
        S.anims.delete(a);
        if (a.rec.anims) a.rec.anims.delete(a);
        a.done && a.done();
      }
    }
    if (S.anims.size > 0) raf = requestAnimationFrame(tick);
    else {
      endRun();
      idleCheck();
    }
  }

  // Quality falls back when frames get slow or the scene gets crowded: the glow goes first.
  function measure(dt) {
    if (!dt || dt > 250) return;
    S.frameTimes.push(dt);
    if (S.frameTimes.length > 60) S.frameTimes.shift();
    const avg = S.frameTimes.reduce((s, x) => s + x, 0) / S.frameTimes.length;
    const low = S.objects.size > 180 || (S.frameTimes.length >= 45 && avg > 22);
    if (low !== S.lowq) {
      S.lowq = low;
      body.classList.toggle("lowq", low);
      report({ type: "quality", low, avg_frame_ms: Math.round(avg * 10) / 10, objects: S.objects.size });
    }
  }

  // ---------------------------------------------------------------- highlight, focus, transform
  function splitRef(ref) {
    const i = ref.indexOf("#");
    return i < 0 ? [ref, null] : [ref.slice(0, i), ref.slice(i + 1)];
  }

  function refEls(ref) {
    const [id, term] = splitRef(ref);
    const rec = S.objects.get(id);
    if (!rec) return [];
    if (term) return Array.from(rec.root.querySelectorAll("tspan")).filter((s) => s.dataset.term === term);
    return [rec.root];
  }

  function highlight(ref, color, pulse, ms) {
    const els = refEls(ref);
    if (!els.length) throw new Error(`nothing called ${ref} to highlight`);
    const [, term] = splitRef(ref);
    for (const e of els) {
      e.style.setProperty("--hl", paint(color, "attention"));
      e.classList.add(term ? "hl-text" : "hl");
      if (pulse && !reduced()) {
        e.classList.remove("hl-pulse");
        void e.getBoundingClientRect();
        e.classList.add("hl-pulse");
      }
    }
    const old = S.highlights.get(ref);
    if (old) clearTimeout(old);
    S.highlights.set(ref, ms ? setTimeout(() => unhighlight(ref), ms) : 0);
  }

  function unhighlight(ref) {
    const refs = ref ? [ref] : Array.from(S.highlights.keys());
    for (const r of refs) {
      for (const e of refEls(r)) e.classList.remove("hl", "hl-text", "hl-pulse");
      const t = S.highlights.get(r);
      if (t) clearTimeout(t);
      S.highlights.delete(r);
    }
  }

  function focus(targets) {
    S.focus = targets ? new Set(targets.map((r) => splitRef(r)[0])) : null;
    for (const [id, rec] of S.objects) {
      const keep = !S.focus || S.focus.has(id) || rec.obj.type === "panel" || rec.obj.type === "spotlight";
      rec.root.classList.toggle("dimmed", !keep);
    }
  }

  function applyTransform(t) {
    const target = {
      scale: t.scale !== undefined ? t.scale : S.transform.scale,
      dx: t.dx !== undefined ? t.dx : S.transform.dx,
      dy: t.dy !== undefined ? t.dy : S.transform.dy,
      origin: t.origin || S.transform.origin,
    };
    const from = { ...S.transform };
    const set = (k) => {
      const s = from.scale + (target.scale - from.scale) * k;
      const dx = from.dx + (target.dx - from.dx) * k;
      const dy = from.dy + (target.dy - from.dy) * k;
      const [ox, oy] = target.origin;
      world.setAttribute("transform", `translate(${dx + ox} ${dy + oy}) scale(${s}) translate(${-ox} ${-oy})`);
    };
    S.transform = target;
    const ms = reduced() ? 0 : t.ms === undefined ? 350 : t.ms;
    if (!ms) return set(1);
    addAnim({ rec: { anims: new Set() }, t0: performance.now(), dur: ms, ease: G.EASE.inout, timeline: "", apply: set });
  }

  function applyTheme(theme) {
    S.theme = { ...S.theme, ...theme };
    body.dataset.palette = S.theme.palette || "jarvis";
    body.style.setProperty("--glow", String(S.theme.palette === "contrast" ? 0 : S.theme.glow));
    body.classList.toggle("reduced", reduced());
  }

  // ---------------------------------------------------------------- clearing
  function clear(groupId, keep) {
    const keepSet = new Set(keep || []);
    for (const [id, rec] of Array.from(S.objects)) {
      const g = rec.obj.group || "jarvis";
      if (groupId ? g === groupId : !keepSet.has(g)) destroy(id);
    }
    if (groupId === "manual" || (!groupId && !keepSet.has("manual"))) Pen.clear(false);
    if (!groupId) {
      for (const [gid, g] of Array.from(S.groups)) {
        if (!keepSet.has(gid) && g.childElementCount === 0) {
          g.remove();
          S.groups.delete(gid);
        }
      }
      unhighlight(null);
      focus(null);
      S.anchors.clear();
      if (!keep || keep.length === 0) {
        S.transform = { scale: 1, dx: 0, dy: 0, origin: [0, 0] };
        world.removeAttribute("transform");
      }
    }
  }

  function dropPending(gen) {
    S.pending = S.pending.filter((p) => {
      if (gen === undefined || p.gen < gen) {
        clearTimeout(p.timer);
        return false;
      }
      return true;
    });
  }

  // Emergency: everything off the screen now, no fades, nothing left waiting to draw.
  function dismiss() {
    dropPending();
    for (const a of S.anims) if (a.done) try { a.done(); } catch { /* going anyway */ }
    S.anims.clear();
    S.objects.forEach((rec) => rec.root.remove());
    S.objects.clear();
    for (const g of S.groups.values()) g.remove();
    S.groups.clear();
    S.anchors.clear();
    S.highlights.forEach((t) => t && clearTimeout(t));
    S.highlights.clear();
    S.focus = null;
    S.pausedAll = false;
    S.paused.clear();
    world.removeAttribute("transform");
    S.transform = { scale: 1, dx: 0, dy: 0, origin: [0, 0] };
    Pen.clear(false);
    Pen.set(false, true);
  }

  // ---------------------------------------------------------------- commands
  function cancelTimeline(id) {
    for (const a of Array.from(S.anims)) {
      if (id && a.timeline !== id) continue;
      if (!a.rec.obj) continue;
      // Something still being drawn when its explanation was cancelled is half a picture:
      // it goes, rather than being left frozen mid-stroke.
      destroy(a.rec.obj.id);
    }
    dropPending();
  }

  const OPS = {
    "scene.create": (c) => {
      clear(null, ["manual"]);
      S.scene = c.scene;
      if (c.theme) applyTheme(c.theme);
      for (const o of c.objects || []) add(o, true);
    },
    "scene.update": (c) => {
      if (c.theme) {
        applyTheme(c.theme);
        for (const [id, rec] of Array.from(S.objects)) replace(id, rec.obj);
      }
      if (c.transform) applyTransform(c.transform);
    },
    "scene.clear": (c) => clear(c.group, c.keep_groups),
    "shape.add": (c) => add(c.object, true),
    "stroke.draw": (c) => add(c.object, true),
    "shape.update": (c) => {
      const rec = S.objects.get(c.id);
      if (!rec) throw new Error(`no object ${c.id}`);
      const merged = { ...rec.obj, ...c.patch };
      if (c.patch.style) merged.style = { ...(rec.obj.style || {}), ...c.patch.style };
      V.object(merged, "merged");
      const r = replace(c.id, merged);
      if (c.patch.anim && r) startAnim(r, c.patch.anim);
      if (merged.type !== "edge") relayoutEdges(c.id);
    },
    "shape.remove": (c) => remove(c.id, c.fade_ms),
    "group.add": (c) => {
      const g = group(c.id);
      g.style.display = c.visible === false ? "none" : "";
      g.style.opacity = c.dim === undefined ? "" : String(1 - c.dim);
    },
    "group.remove": (c) => clear(c.id),
    "highlight.show": (c) => highlight(c.target, c.color, c.pulse, c.ms),
    "highlight.hide": (c) => unhighlight(c.target),
    "focus.show": (c) => focus(c.targets),
    "focus.hide": () => focus(null),
    "timeline.play": (c) => { S.paused.delete(c.timeline); S.pausedAll = false; loop(); },
    "timeline.pause": (c) => {
      if (c.timeline) S.paused.add(c.timeline);
      else S.pausedAll = true;
      body.classList.add("paused");
    },
    "timeline.resume": (c) => {
      const go = () => {
        if (c.timeline) S.paused.delete(c.timeline);
        else { S.pausedAll = false; S.paused.clear(); }
        body.classList.remove("paused");
        loop();
      };
      const wait = c.at ? c.at - Date.now() : 0;
      if (wait > 3) S.pending.push({ timer: setTimeout(go, wait), gen: S.gen });
      else go();
    },
    "timeline.seek": (c) => {
      const now = performance.now();
      for (const a of S.anims) if (a.timeline === c.timeline) a.t0 = now - c.to_ms;
      loop();
    },
    "timeline.cancel": (c) => cancelTimeline(c.timeline),
    "anchor.attach": (c) => {
      const a = c.anchor;
      const known = S.anchors.get(a.id);
      if (!known && S.anchors.size >= LIMITS.max_anchors) throw new Error("too many anchors");
      S.anchors.set(a.id, { a, base: known ? known.base : a.bounds });
      for (const rec of S.objects.values()) if (rec.obj.anchor === a.id) placeAnchored(rec);
      watchAnchors();
    },
    "anchor.detach": (c) => {
      S.anchors.delete(c.id);
      for (const rec of S.objects.values()) if (rec.obj.anchor === c.id) rec.root.classList.add("hidden");
    },
  };

  function apply(batch, recvAt) {
    if (batch.gen < S.gen) {
      report({ type: "stale", gen: batch.gen, seq: batch.seq });
      return;
    }
    S.lesson = batch.lesson;
    let failed = 0;
    for (const cmd of batch.cmds) {
      try {
        OPS[cmd.op](cmd);
      } catch (e) {
        failed++;
        report({ type: "error", op: cmd.op, why: String(e.message || e).slice(0, 120), gen: batch.gen, seq: batch.seq });
      }
    }
    loop();
    requestAnimationFrame((t) => {
      const at = epoch(t);
      report({
        type: "frame", lesson: batch.lesson, gen: batch.gen, seq: batch.seq, failed,
        drift_ms: batch.at ? Math.round(at - batch.at) : null,
        cmd_ms: recvAt ? Math.round(at - recvAt) : null,
        e2e_ms: batch.sent ? Math.round(at - batch.sent) : null,
        objects: S.objects.size,
      });
    });
    idleCheck();
  }

  function receive(batch, recvAt) {
    try {
      V.envelope(batch);
    } catch (e) {
      report({ type: "rejected", why: String(e.message || e).slice(0, 160) });
      return;
    }
    if (batch.gen < S.gen) {
      report({ type: "stale", gen: batch.gen, seq: batch.seq });
      return;
    }
    if (batch.gen > S.gen) {
      // A newer lesson (or a cancel): nothing scheduled by an older one may still land.
      S.gen = batch.gen;
      dropPending(batch.gen);
    }
    const wait = batch.at ? batch.at - Date.now() : 0;
    if (wait > 3) {
      const entry = { gen: batch.gen, timer: 0 };
      entry.timer = setTimeout(() => {
        S.pending = S.pending.filter((p) => p !== entry);
        apply(batch, recvAt);
      }, wait);
      S.pending.push(entry);
    } else {
      apply(batch, recvAt);
    }
  }

  let idleTimer = 0;
  function idleCheck() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      const idle = S.objects.size === 0 && S.anims.size === 0 && S.pending.length === 0 && Pen.empty() && !Pen.on;
      if (idle) report({ type: "idle" });
    }, 150);
  }

  // ---------------------------------------------------------------- manual pen
  const Pen = (() => {
    const ctx = ink.getContext("2d");
    const P = { on: false, tool: "pen", strokes: [], redo: [], live: null, idle: 0, ratio: 1 };
    const COLORS = { pen: "#4cd4ff", highlighter: "rgba(255, 194, 74, 0.38)" };

    function resize() {
      const { w, h, ratio } = G.canvasSize(window.innerWidth, window.innerHeight, window.devicePixelRatio);
      ink.width = w;
      ink.height = h;
      P.ratio = ratio;
      redraw();
    }

    function drawStroke(s) {
      if (s.points.length === 0) return;
      ctx.save();
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width;
      if (s.tool === "pen") {
        ctx.shadowColor = "rgba(76, 212, 255, 0.45)";
        ctx.shadowBlur = 6;
      }
      ctx.beginPath();
      ctx.moveTo(s.points[0][0], s.points[0][1]);
      for (let i = 1; i < s.points.length - 1; i++) {
        const [x, y] = s.points[i];
        const [nx, ny] = s.points[i + 1];
        ctx.quadraticCurveTo(x, y, (x + nx) / 2, (y + ny) / 2);
      }
      const last = s.points[s.points.length - 1];
      ctx.lineTo(last[0], last[1]);
      ctx.stroke();
      ctx.restore();
    }

    function redraw() {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, ink.width, ink.height);
      // Physical pixels underneath, CSS pixels on top: sharp at every scale.
      ctx.setTransform(P.ratio, 0, 0, P.ratio, 0, 0);
      for (const s of P.strokes) drawStroke(s);
      if (P.live) drawStroke(P.live);
    }

    function point(e) {
      return [e.offsetX, e.offsetY];
    }

    ink.addEventListener("pointerdown", (e) => {
      if (!P.on) return;
      ink.setPointerCapture(e.pointerId);
      touch();
      if (P.tool === "eraser") {
        erase(point(e));
        return;
      }
      const pressure = e.pointerType === "pen" && e.pressure > 0 ? e.pressure : 0.5;
      P.live = { tool: P.tool, color: COLORS[P.tool], points: [point(e)],
                 width: P.tool === "highlighter" ? 18 : 2 + 4 * pressure };
    });
    ink.addEventListener("pointermove", (e) => {
      if (!P.on) return;
      if (P.tool === "eraser" && e.buttons) {
        erase(point(e));
        return;
      }
      if (!P.live) return;
      for (const ev of e.getCoalescedEvents ? e.getCoalescedEvents() : [e]) {
        if (P.live.points.length < LIMITS.max_points) P.live.points.push(point(ev));
      }
      redraw();
    });
    const finish = () => {
      if (!P.live) return;
      P.strokes.push(P.live);
      P.redo = [];
      P.live = null;
      redraw();
      touch();
    };
    ink.addEventListener("pointerup", finish);
    ink.addEventListener("pointercancel", finish);

    function erase(p) {
      const i = G.strokeAt(P.strokes, p, 14);
      if (i >= 0) {
        P.redo = [];
        P.strokes.splice(i, 1);
        redraw();
      }
    }

    // The overlay must never be left holding the mouse. Two minutes without a stroke and pen
    // mode ends by itself.
    function touch() {
      clearTimeout(P.idle);
      if (P.on) P.idle = setTimeout(() => set(false), 120000);
    }

    function set(on, quiet) {
      const was = P.on;
      P.on = !!on;
      body.classList.toggle("pen", P.on);
      penBar.hidden = !P.on;
      touch();
      if (!P.on) P.live = null;
      if (was !== P.on && !quiet) api.pen(P.on);
      tool(P.tool);
      idleCheck();
    }

    function tool(name) {
      P.tool = name;
      body.classList.toggle("eraser", name === "eraser");
      for (const b of penBar.querySelectorAll("button[data-tool]")) b.setAttribute("aria-pressed", String(b.dataset.tool === name));
    }

    function clear(record) {
      if (record && P.strokes.length) P.redo = [];
      P.strokes = [];
      P.live = null;
      redraw();
    }

    penBar.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      touch();
      if (b.dataset.tool) tool(b.dataset.tool);
      else if (b.dataset.act === "undo" && P.strokes.length) { P.redo.push(P.strokes.pop()); redraw(); }
      else if (b.dataset.act === "redo" && P.redo.length) { P.strokes.push(P.redo.pop()); redraw(); }
      else if (b.dataset.act === "clear") clear(true);
      else if (b.dataset.act === "exit") set(false);
    });

    window.addEventListener("resize", resize);
    resize();
    return {
      set, tool, clear, resize,
      undo: () => { if (P.strokes.length) { P.redo.push(P.strokes.pop()); redraw(); } },
      redo: () => { if (P.redo.length) { P.strokes.push(P.redo.pop()); redraw(); } },
      empty: () => P.strokes.length === 0,
      get on() { return P.on; },
    };
  })();

  // ---------------------------------------------------------------- wiring
  function setGeometry(g) {
    S.geometry = g;
    // The viewBox starts where the window starts on its monitor, so monitor coordinates are
    // used as they are.
    stage.setAttribute("viewBox", `${g.offset.x} ${g.offset.y} ${g.w} ${g.h}`);
    Pen.resize();
  }

  api.onGeometry(setGeometry);
  api.onBatch(receive);
  api.onControl((action, arg) => {
    if (action === "dismiss") dismiss();
    else if (action === "pen") Pen.set(!!arg, true);
    else if (action === "pen-tool" && ["pen", "highlighter", "eraser"].includes(arg)) Pen.tool(arg);
    else if (action === "pen-undo") Pen.undo();
    else if (action === "pen-redo") Pen.redo();
    else if (action === "visible") {
      // Hidden: nothing is drawn, so nothing needs to tick.
      if (!arg && raf) { cancelAnimationFrame(raf); raf = 0; }
      if (arg) loop();
    }
  });
  reducedQuery.addEventListener("change", () => applyTheme({}));
  applyTheme({});
  report({ type: "ready" });
})();
