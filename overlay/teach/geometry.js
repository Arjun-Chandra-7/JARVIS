// Geometry for the teaching overlay, kept free of the DOM so it can be tested under Node.
//
// Coordinates everywhere are logical pixels (GNOME's "logical", Electron's DIP) relative to the
// top-left of the monitor the scene is on. The window covers that monitor's work area, so the
// renderer's viewBox starts at the work area's offset and a coordinate never needs converting
// twice. Physical pixels appear in exactly one place: the manual-ink canvas, sized by
// devicePixelRatio so strokes stay sharp at 125 % or 150 %.
"use strict";

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.TeachGeometry = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const r2 = (n) => Math.round(n * 100) / 100;

  // A smooth path through freehand points: quadratic segments through the midpoints, so a
  // wobbly stroke reads as a pen line and not a polyline.
  function smoothPath(points) {
    if (!points || points.length === 0) return "";
    if (points.length === 1) return `M${r2(points[0][0])},${r2(points[0][1])}`;
    if (points.length === 2) {
      return `M${r2(points[0][0])},${r2(points[0][1])}L${r2(points[1][0])},${r2(points[1][1])}`;
    }
    let d = `M${r2(points[0][0])},${r2(points[0][1])}`;
    for (let i = 1; i < points.length - 1; i++) {
      const [x, y] = points[i];
      const [nx, ny] = points[i + 1];
      d += `Q${r2(x)},${r2(y)} ${r2((x + nx) / 2)},${r2((y + ny) / 2)}`;
    }
    const last = points[points.length - 1];
    return d + `L${r2(last[0])},${r2(last[1])}`;
  }

  function polygonPath(points) {
    return points.map((p, i) => `${i ? "L" : "M"}${r2(p[0])},${r2(p[1])}`).join("") + "Z";
  }

  // A line, straight or bent. `bend` is the control point's offset as a fraction of half the
  // length, perpendicular to the line; the tangent at the end is what orients an arrowhead.
  function curve(from, to, bend) {
    const [x1, y1] = from;
    const [x2, y2] = to;
    const b = bend || 0;
    if (!b) {
      return { d: `M${r2(x1)},${r2(y1)}L${r2(x2)},${r2(y2)}`, mid: [(x1 + x2) / 2, (y1 + y2) / 2], tangent: [x2 - x1, y2 - y1] };
    }
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const len = Math.hypot(x2 - x1, y2 - y1) || 1;
    const nx = -(y2 - y1) / len;
    const ny = (x2 - x1) / len;
    const cx = mx + nx * b * len / 2;
    const cy = my + ny * b * len / 2;
    return {
      d: `M${r2(x1)},${r2(y1)}Q${r2(cx)},${r2(cy)} ${r2(x2)},${r2(y2)}`,
      mid: [0.25 * x1 + 0.5 * cx + 0.25 * x2, 0.25 * y1 + 0.5 * cy + 0.25 * y2],
      tangent: [x2 - cx, y2 - cy],
    };
  }

  // The arrowhead at `tip`, pointing along `tangent`. Returned as the shortened end of the shaft
  // too, so a thick line never pokes through the point of its own arrow.
  function arrowHead(tip, tangent, size) {
    const s = size || 14;
    const len = Math.hypot(tangent[0], tangent[1]) || 1;
    const ux = tangent[0] / len;
    const uy = tangent[1] / len;
    const bx = tip[0] - ux * s;
    const by = tip[1] - uy * s;
    const w = s * 0.55;
    return {
      d: polygonPath([tip, [bx - uy * w, by + ux * w], [bx + uy * w, by - ux * w]]),
      shaftEnd: [tip[0] - ux * s * 0.6, tip[1] - uy * s * 0.6],
    };
  }

  // The small square that marks a right angle: from the vertex, `size` along each side.
  function rightAngle(at, a, b, size) {
    const s = size || 22;
    const unit = (p) => {
      const dx = p[0] - at[0];
      const dy = p[1] - at[1];
      const l = Math.hypot(dx, dy) || 1;
      return [dx / l, dy / l];
    };
    const [ux, uy] = unit(a);
    const [vx, vy] = unit(b);
    const p1 = [at[0] + ux * s, at[1] + uy * s];
    const p2 = [at[0] + (ux + vx) * s, at[1] + (uy + vy) * s];
    const p3 = [at[0] + vx * s, at[1] + vy * s];
    return `M${r2(p1[0])},${r2(p1[1])}L${r2(p2[0])},${r2(p2[1])}L${r2(p3[0])},${r2(p3[1])}`;
  }

  // Where the line from a box's centre toward `toward` leaves the box — so an edge starts at a
  // node's border, not under its label.
  function borderPoint(rect, toward, gap) {
    const cx = rect.x + rect.w / 2;
    const cy = rect.y + rect.h / 2;
    const dx = toward[0] - cx;
    const dy = toward[1] - cy;
    if (!dx && !dy) return [cx, cy];
    const tx = dx ? (rect.w / 2) / Math.abs(dx) : Infinity;
    const ty = dy ? (rect.h / 2) / Math.abs(dy) : Infinity;
    const t = Math.min(tx, ty);
    const g = gap || 0;
    const l = Math.hypot(dx, dy);
    return [cx + dx * t + (dx / l) * g, cy + dy * t + (dy / l) * g];
  }

  function edgeBetween(a, b, gap) {
    const ca = [a.x + a.w / 2, a.y + a.h / 2];
    const cb = [b.x + b.w / 2, b.y + b.h / 2];
    return { from: borderPoint(a, cb, gap), to: borderPoint(b, ca, gap) };
  }

  // Monitor-relative logical coordinates → the window's own CSS pixels.
  function toWindow(point, offset) {
    return [point[0] - offset.x, point[1] - offset.y];
  }

  // Canvas backing-store size for crisp ink: physical pixels, rounded, never zero.
  function canvasSize(cssW, cssH, dpr) {
    const ratio = dpr > 0 && Number.isFinite(dpr) ? dpr : 1;
    return { w: Math.max(1, Math.round(cssW * ratio)), h: Math.max(1, Math.round(cssH * ratio)), ratio };
  }

  // Where an anchored object sits now: its original placement, moved and scaled with the
  // anchor's bounds since the object was attached.
  function anchorTransform(base, now) {
    const sx = base.w ? now.w / base.w : 1;
    const sy = base.h ? now.h / base.h : 1;
    return `translate(${r2(now.x)} ${r2(now.y)}) scale(${r2(sx * 1000) / 1000} ${r2(sy * 1000) / 1000}) translate(${r2(-base.x)} ${r2(-base.y)})`;
  }

  function distToSegment(p, a, b) {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const l2 = dx * dx + dy * dy;
    let t = l2 ? ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
  }

  // The stroke under the eraser, if any: nearest within `radius` of any of its segments.
  function strokeAt(strokes, p, radius) {
    let best = -1;
    let bestD = radius;
    strokes.forEach((s, i) => {
      for (let j = 1; j < s.points.length; j++) {
        const d = distToSegment(p, s.points[j - 1], s.points[j]) - (s.width || 0) / 2;
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      }
      if (s.points.length === 1 && Math.hypot(p[0] - s.points[0][0], p[1] - s.points[0][1]) < bestD) best = i;
    });
    return best;
  }

  const EASE = {
    linear: (t) => t,
    out: (t) => 1 - Math.pow(1 - t, 3),
    inout: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  };

  return { smoothPath, polygonPath, curve, arrowHead, rightAngle, borderPoint, edgeBetween, toWindow,
           canvasSize, anchorTransform, strokeAt, distToSegment, EASE };
});
