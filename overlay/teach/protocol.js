// The teaching overlay's command protocol, checked in the Electron main process before anything
// reaches the renderer — and again in the renderer for objects it assembles from patches.
//
// It interprets overlay/teach/protocol.json exactly as jarvis/teach/protocol.py does; the two are
// tested against the same cases. Python validated the batch before sending it, but this side
// does not care: /emit is a local HTTP endpoint any process can post to, and the renderer is the
// one place in Jarvis where a string could become pixels over everything else on the screen.
"use strict";

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.TeachProtocol = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const CONTROL = /[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]/;
  const UNSAFE_TEXT = new RegExp(
    "(?:[a-z][a-z0-9+.-]*:\\s*//|\\bjavascript\\s*:|\\bdata\\s*:|\\bfile\\s*:|\\bblob\\s*:|\\bvbscript\\s*:|" +
    "<\\s*/?\\s*[a-z!?]|(?:^|[\\s(\"'])(?:~|\\.{1,2})?/(?:home|etc|usr|var|tmp|proc|root|dev|opt|srv|mnt|run)\\b|" +
    "\\\\\\\\|[A-Za-z]:\\\\)", "i");
  const HEX = /^#[0-9a-fA-F]{6}$/;

  class ProtocolError extends Error {
    constructor(path, why) {
      super(`${path}: ${why}`);
      this.path = path;
      this.why = why;
    }
  }

  function isNum(v) {
    return typeof v === "number" && Number.isFinite(v);
  }

  function range(arg) {
    const [lo, hi] = arg.split("..").map(Number);
    return [lo, hi];
  }

  function make(spec, clock) {
    const L = spec.limits;
    const now = clock || (() => Date.now());
    const ID = new RegExp(`^[A-Za-z0-9_.:-]{1,${L.max_id}}$`);
    const REF = new RegExp(`^[A-Za-z0-9_.:-]{1,${L.max_id}}(?:#[A-Za-z0-9_.:-]{1,${L.max_id}})?$`);
    const colors = new Set(spec.colors);
    const types = Object.keys(spec.objects).filter((k) => !k.startsWith("_"));
    const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
    const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

    function check(t, v, path) {
      if (t.startsWith("?")) return v === undefined || v === null ? v : check(t.slice(1), v, path);
      const i = t.indexOf(":");
      const name = i < 0 ? t : t.slice(0, i);
      const arg = i < 0 ? "" : t.slice(i + 1);
      switch (name) {
        case "id":
          if (typeof v !== "string" || !ID.test(v)) throw new ProtocolError(path, "must be a short identifier");
          return v;
        case "ids":
          if (!Array.isArray(v) || v.length > 64) throw new ProtocolError(path, "must be a list of at most 64 identifiers");
          v.forEach((x, j) => check("id", x, `${path}[${j}]`));
          return v;
        case "ref":
          if (typeof v !== "string" || !REF.test(v)) throw new ProtocolError(path, "must be an object id, optionally #term");
          return v;
        case "refs":
          if (!Array.isArray(v) || v.length < 1 || v.length > 32) throw new ProtocolError(path, "must be 1–32 references");
          v.forEach((x, j) => check("ref", x, `${path}[${j}]`));
          return v;
        case "bool":
          if (typeof v !== "boolean") throw new ProtocolError(path, "must be true or false");
          return v;
        case "int":
        case "num": {
          if (!isNum(v)) throw new ProtocolError(path, "must be a finite number");
          if (name === "int" && !Number.isInteger(v)) throw new ProtocolError(path, "must be a whole number");
          const [lo, hi] = range(arg);
          if (v < lo || v > hi) throw new ProtocolError(path, `must be between ${arg}`);
          return v;
        }
        case "unit": return check("num:0..1", v, path);
        case "ms": return check(`num:0..${L.max_anim_ms}`, v, path);
        case "delay": return check(`num:0..${L.max_delay_ms}`, v, path);
        case "coord": return check(`num:${-L.max_coord}..${L.max_coord}`, v, path);
        case "size":
          if (!isNum(v)) throw new ProtocolError(path, "must be a finite number");
          if (!(v > 0 && v <= L.max_size)) throw new ProtocolError(path, "must be a positive size within the screen");
          return v;
        case "epoch": {
          if (!isNum(v)) throw new ProtocolError(path, "must be a finite number");
          const n = now();
          if (v < n - 86400000 || v > n + L.max_schedule_ahead_ms) throw new ProtocolError(path, "is not a time close to now");
          return v;
        }
        case "point":
          if (!Array.isArray(v) || v.length !== 2) throw new ProtocolError(path, "must be [x, y]");
          check("coord", v[0], `${path}[0]`);
          check("coord", v[1], `${path}[1]`);
          return v;
        case "points":
          if (!Array.isArray(v) || v.length < 2 || v.length > L.max_points) {
            throw new ProtocolError(path, `must be 2–${L.max_points} points`);
          }
          v.forEach((p, j) => check("point", p, `${path}[${j}]`));
          return v;
        case "tri": {
          if (!Array.isArray(v) || v.length !== 3) throw new ProtocolError(path, "a triangle has three points");
          v.forEach((p, j) => check("point", p, `${path}[${j}]`));
          const [[ax, ay], [bx, by], [cx, cy]] = v;
          if (Math.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2 < 4) {
            throw new ProtocolError(path, "is not a triangle (its points are in a line)");
          }
          return v;
        }
        case "rect": {
          const keys = isObj(v) ? Object.keys(v).sort().join(",") : "";
          if (keys !== "h,w,x,y") throw new ProtocolError(path, "must be {x, y, w, h}");
          check("coord", v.x, `${path}.x`);
          check("coord", v.y, `${path}.y`);
          check("size", v.w, `${path}.w`);
          check("size", v.h, `${path}.h`);
          return v;
        }
        case "monitor":
          if (v === "primary" || v === "pointer") return v;
          return check(`int:0..${L.max_monitor}`, v, path);
        case "text":
          if (typeof v !== "string" || v.length > L.max_text) {
            throw new ProtocolError(path, `must be text of at most ${L.max_text} characters`);
          }
          if (CONTROL.test(v)) throw new ProtocolError(path, "contains control characters");
          if (UNSAFE_TEXT.test(v)) throw new ProtocolError(path, "looks like a link, markup or a file path");
          return v;
        case "color":
          if (typeof v !== "string" || !(colors.has(v) || HEX.test(v))) {
            throw new ProtocolError(path, "must be a palette name or #rrggbb");
          }
          return v;
        case "enum":
          if (typeof v === "boolean" || !arg.split(",").includes(String(v))) throw new ProtocolError(path, `must be one of ${arg}`);
          return v;
        case "otype":
          if (!types.includes(v)) throw new ProtocolError(path, "is not a known object type");
          return v;
        case "style": case "anim": case "theme": case "transform": case "anchor": case "term":
          return struct(spec[name], v, path, name);
        case "terms":
          if (!Array.isArray(v) || v.length < 1 || v.length > L.max_terms) {
            throw new ProtocolError(path, `must be 1–${L.max_terms} terms`);
          }
          v.forEach((x, j) => check("term", x, `${path}[${j}]`));
          return v;
        case "object": return object(v, path);
        case "objects":
          if (!Array.isArray(v) || v.length > L.max_objects) throw new ProtocolError(path, `must be at most ${L.max_objects} objects`);
          v.forEach((o, j) => object(o, `${path}[${j}]`));
          return v;
        case "patch": return patch(v, path);
        case "commands":
          if (!Array.isArray(v) || v.length < 1 || v.length > L.max_batch_commands) {
            throw new ProtocolError(path, `must be 1–${L.max_batch_commands} commands`);
          }
          v.forEach((c, j) => command(c, `${path}[${j}]`));
          return v;
        default:
          throw new ProtocolError(path, `unknown type ${JSON.stringify(t)} in the protocol`);
      }
    }

    function struct(fields, v, path, what) {
      if (!isObj(v)) throw new ProtocolError(path, `${what} must be an object`);
      for (const k of Object.keys(v)) {
        if (!own(fields, k)) throw new ProtocolError(path, `unknown field ${JSON.stringify(k)}`);
      }
      for (const [k, t] of Object.entries(fields)) {
        if (!t.startsWith("?") && !own(v, k)) throw new ProtocolError(`${path}.${k}`, "is required");
        if (own(v, k)) check(t, v[k], `${path}.${k}`);
      }
      if (what === "anchor" && now() - v.observed_at > L.max_anchor_age_ms) {
        throw new ProtocolError(`${path}.observed_at`, "the anchor is stale");
      }
      return v;
    }

    function object(v, path) {
      if (!isObj(v) || !types.includes(v.type)) throw new ProtocolError(`${path}.type`, "is not a known object type");
      return struct({ ...spec.objects._common, ...spec.objects[v.type] }, v, path, "object");
    }

    function patch(v, path, otype) {
      if (!isObj(v) || Object.keys(v).length === 0) throw new ProtocolError(path, "must be a non-empty object");
      for (const [k, value] of Object.entries(v)) {
        if (!spec.patchable.includes(k)) throw new ProtocolError(`${path}.${k}`, "cannot be changed");
        const candidates = otype
          ? [spec.objects[otype][k] || spec.objects._common[k]]
          : Object.values(spec.objects).filter((f) => own(f, k)).map((f) => f[k]);
        let first = null;
        let ok = false;
        for (const t of candidates) {
          if (!t) continue;
          try {
            check(t.replace(/^\?/, ""), value, `${path}.${k}`);
            ok = true;
            break;
          } catch (e) {
            first = first || e;
          }
        }
        if (!ok) throw first || new ProtocolError(`${path}.${k}`, "cannot be changed");
      }
      return v;
    }

    function command(v, path) {
      if (!isObj(v) || typeof v.op !== "string") throw new ProtocolError(`${path}.op`, "is missing");
      if (!own(spec.ops, v.op)) throw new ProtocolError(`${path}.op`, `unknown command ${JSON.stringify(v.op.slice(0, 40))}`);
      struct({ op: `enum:${v.op}`, ...spec.ops[v.op] }, v, path, "command");
      if (v.op === "stroke.draw" && !["stroke", "highlighter"].includes(v.object.type)) {
        throw new ProtocolError(`${path}.object.type`, "stroke.draw draws strokes only");
      }
      return v;
    }

    function envelope(v) {
      let raw;
      try {
        raw = JSON.stringify(v);
      } catch {
        throw new ProtocolError("batch", "is not plain data");
      }
      // UTF-8 length, the same measure Python uses.
      const bytes = typeof TextEncoder !== "undefined" ? new TextEncoder().encode(raw).length : raw.length * 3;
      if (bytes > L.max_payload_bytes) throw new ProtocolError("batch", "is too large");
      return struct(spec.envelope, v, "batch", "envelope");
    }

    return { check, object, patch, command, envelope, limits: L, types };
  }

  return { make, ProtocolError };
});
