// The teaching layer, main-process side: one transparent, click-through window over the work
// area of one monitor, which exists from startup but is only mapped while there is something on
// it.
//
// Commands come from the backend's event stream (kind "teach"), exactly like the dictation
// capsule's, and are validated here before the renderer sees them. What the renderer reports
// (frames drawn, drift, idle) goes back as kind "teach_event", so the voice process can measure
// speech against pictures and learn about an emergency dismissal.
//
// Invariants, because an overlay that swallows the desktop is worse than no overlay:
//   * The window never takes focus (focusable: false) and ignores the mouse, except in pen mode.
//   * Hidden means unmapped: a hidden window intercepts nothing and draws nothing.
//   * Pen mode always ends — by its own button, the shortcut, two idle minutes in the renderer,
//     or a five-minute ceiling here — and ending it always restores click-through.
//   * Emergency dismissal does not wait for anyone: the window is hidden first, then the
//     renderer and the backend are told.
"use strict";

const http = require("http");
const net = require("net");
const fs = require("fs");
const path = require("path");
const { make } = require("./protocol.js");

const SPEC = JSON.parse(fs.readFileSync(path.join(__dirname, "protocol.json"), "utf8"));
const CONTROL_ACTIONS = new Set(["dismiss", "pen", "pen-on", "pen-off", "reset", "pen-undo", "pen-redo", "displays", "state", "clear"]);
const DEFAULT_HOLD_S = 150;
const PEN_CEILING_MS = 5 * 60 * 1000;

function setup({ app, BrowserWindow, ipcMain, screen, globalShortcut, port, shortcuts, log }) {
  const V = make(SPEC);
  const say = log || (() => {});
  let win = null;
  let shown = false;
  let pen = false;
  let penTimer = null;
  let monitor = null;           // the Display the scene is on
  let quitting = false;
  let holdS = DEFAULT_HOLD_S;
  let holdTimer = null;
  const stats = { batches: 0, rejected: 0, dismissals: 0 };

  // ------------------------------------------------------------------ backend
  function post(kind, payload) {
    const body = JSON.stringify({ kind, text: typeof payload === "string" ? payload : JSON.stringify(payload) });
    const req = http.request({ host: "127.0.0.1", port, path: "/emit", method: "POST", timeout: 1500,
                               headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } });
    req.on("error", () => {});
    req.on("timeout", () => req.destroy());
    req.end(body);
  }

  function stopSpeech() {
    const req = http.request({ host: "127.0.0.1", port, path: "/speech/stop", method: "POST", timeout: 1500 });
    req.on("error", () => {});
    req.on("timeout", () => req.destroy());
    req.end();
  }

  // ------------------------------------------------------------------ displays
  function displays() {
    return screen.getAllDisplays().slice().sort((a, b) => a.bounds.x - b.bounds.x || a.bounds.y - b.bounds.y);
  }

  function pickDisplay(m) {
    if (m === "primary" || m === undefined) return screen.getPrimaryDisplay();
    if (m === "pointer") return screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
    return displays()[m] || null;
  }

  function describeDisplays() {
    const primary = screen.getPrimaryDisplay().id;
    return displays().map((d, index) => ({
      index, primary: d.id === primary, scale: d.scaleFactor,
      bounds: d.bounds, work: d.workArea,
    }));
  }

  function place(d) {
    if (!win || win.isDestroyed()) return;
    monitor = d;
    const wa = d.workArea;
    const b = win.getBounds();
    // The work area, not the whole monitor: a borderless always-on-top window exactly the size
    // of the screen is what Mutter treats as a fullscreen app, and it hides the top bar for it.
    if (b.x !== wa.x || b.y !== wa.y || b.width !== wa.width || b.height !== wa.height) {
      win.setBounds({ x: wa.x, y: wa.y, width: wa.width, height: wa.height });
    }
    win.webContents.send("teach-geometry", {
      offset: { x: wa.x - d.bounds.x, y: wa.y - d.bounds.y }, w: wa.width, h: wa.height, scale: d.scaleFactor,
    });
  }

  // ------------------------------------------------------------------ window
  function create() {
    const d = screen.getPrimaryDisplay();
    const wa = d.workArea;
    win = new BrowserWindow({
      x: wa.x, y: wa.y, width: wa.width, height: wa.height,
      show: false,
      transparent: true,
      frame: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      focusable: false,
      skipTaskbar: true,
      hasShadow: false,
      fullscreenable: false,
      // Fully transparent: if a compositor refused alpha here, any opaque colour would cover
      // the whole work area.
      backgroundColor: "#00000000",
      webPreferences: {
        preload: path.join(__dirname, "..", "teach-preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        spellcheck: false,
        webSecurity: true,
      },
    });
    win.setIgnoreMouseEvents(true);
    win.setAlwaysOnTop(true, "screen-saver");
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    win.loadFile(path.join(__dirname, "..", "teach.html"));
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("will-navigate", (e) => e.preventDefault());
    win.webContents.once("did-finish-load", () => place(d));
    // A crashed renderer takes its scene with it. A fresh window comes up hidden and the voice
    // process is told, so it can redraw the current step or give up on the lesson.
    win.webContents.on("render-process-gone", (_e, details) => {
      say(`teach renderer gone: ${details.reason}`);
      const old = win;
      win = null;
      shown = false;
      setPen(false, true);
      try { old.destroy(); } catch { /* already gone */ }
      if (!quitting) {
        create();
        post("teach_event", { type: "renderer_gone", reason: String(details.reason) });
      }
    });
    win.on("closed", () => { if (win && win.isDestroyed()) win = null; });
  }

  function show() {
    if (!win || win.isDestroyed()) create();
    if (shown) return;
    shown = true;
    win.setIgnoreMouseEvents(!pen);
    win.showInactive();
    win.setAlwaysOnTop(true, "screen-saver");
    win.webContents.send("teach-control", "visible", true);
    post("teach_event", { type: "visibility", shown: true });
  }

  function hide() {
    if (!win || win.isDestroyed()) return;
    win.setIgnoreMouseEvents(true);
    clearTimeout(holdTimer);
    if (shown) {
      shown = false;
      win.hide();
      win.webContents.send("teach-control", "visible", false);
      post("teach_event", { type: "visibility", shown: false });
    }
  }

  // ------------------------------------------------------------------ pen mode
  function setPen(on, quiet) {
    pen = !!on;
    clearTimeout(penTimer);
    if (win && !win.isDestroyed()) {
      if (pen) {
        show();
        penTimer = setTimeout(() => setPen(false), PEN_CEILING_MS);
      }
      win.setIgnoreMouseEvents(!pen);
      if (!quiet) win.webContents.send("teach-control", "pen", pen);
    }
    post("teach_event", { type: "pen", on: pen });
  }

  // ------------------------------------------------------------------ emergency
  function dismiss(by) {
    const t0 = process.hrtime.bigint();
    setPen(false, true);
    hide();
    if (win && !win.isDestroyed()) win.webContents.send("teach-control", "dismiss");
    stats.dismissals++;
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    post("teach_event", { type: "dismissed", by, ms: Math.round(ms * 100) / 100 });
    stopSpeech();
  }

  // ------------------------------------------------------------------ commands
  function handleBatch(text) {
    const recvAt = Date.now();
    let batch;
    try {
      batch = JSON.parse(text);
      V.envelope(batch);
    } catch (e) {
      stats.rejected++;
      // The reason names a field, never its content: what was on screen is not logged.
      post("teach_event", { type: "rejected", why: String(e.path || "batch") + ": " + String(e.why || "not valid JSON") });
      return;
    }
    const sceneCmd = batch.cmds.find((c) => c.op === "scene.create");
    if (sceneCmd) {
      const d = pickDisplay(sceneCmd.monitor);
      if (!d) {
        stats.rejected++;
        post("teach_event", { type: "rejected", why: "scene.create.monitor: no such monitor" });
        return;
      }
      if (!win || win.isDestroyed()) create();
      place(d);
    }
    stats.batches++;
    for (const c of batch.cmds) {
      if (c.op === "scene.create") holdS = c.hold_s || DEFAULT_HOLD_S;
      else if (c.op === "scene.update" && c.hold_s) holdS = c.hold_s;
    }
    show();
    armHold();
    win.webContents.send("teach-batch", batch, recvAt);
  }

  // A scene nobody is updating goes away by itself. Its owner's own clean-up lives in the process
  // that drew it; when that process has gone — found live: a lesson left on screen after the
  // script that drew it exited, with nothing that could clear it — this is the clean-up.
  // "Leave it" and requested drawings ask for a long hold instead of the default.
  function armHold() {
    clearTimeout(holdTimer);
    holdTimer = setTimeout(() => {
      if (pen || !shown) return;
      hide();
      if (win && !win.isDestroyed()) win.webContents.send("teach-control", "dismiss");
      post("teach_event", { type: "expired", after_s: holdS });
    }, holdS * 1000);
  }

  function handleControl(text) {
    let c;
    try {
      c = JSON.parse(text);
    } catch {
      return;
    }
    if (!c || !CONTROL_ACTIONS.has(c.action)) return;
    if (c.action === "displays") post("teach_event", { type: "displays", displays: describeDisplays() });
    else if (c.action === "state") post("teach_event", { type: "visibility", shown, pen });
    else if (c.action === "clear") {
      // "Clear the screen" from a process that did not draw what is there: it knows nothing to
      // clear, so the overlay does it. Unlike dismiss, the voice is left alone.
      setPen(false, true);
      hide();
      if (win && !win.isDestroyed()) win.webContents.send("teach-control", "dismiss");
    }
    else if (c.action === "dismiss") dismiss(c.by === "voice" ? "voice" : "command");
    else if (c.action === "reset") {
      // A restarted voice process: whatever it drew before is nobody's any more.
      setPen(false, true);
      hide();
      if (win && !win.isDestroyed()) win.webContents.send("teach-control", "dismiss");
    } else if (c.action === "pen") setPen(!pen);
    else if (c.action === "pen-on") setPen(true);
    else if (c.action === "pen-off") setPen(false);
    else if (win && !win.isDestroyed()) win.webContents.send("teach-control", c.action);
  }

  let backoff = 1000;
  function subscribe() {
    if (quitting) return;
    const req = http.get({ host: "127.0.0.1", port, path: "/events", headers: { Accept: "text/event-stream" } }, (res) => {
      backoff = 1000;
      post("teach_event", { type: "displays", displays: describeDisplays() });
      let buf = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        buf += chunk;
        if (buf.length > 2_000_000) buf = "";        // a stream gone wrong is dropped, not buffered forever
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const line = frame.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
          if (!line) continue;
          let msg;
          try { msg = JSON.parse(line); } catch { continue; }
          if (msg.kind === "teach") handleBatch(msg.text);
          else if (msg.kind === "teach_control") handleControl(msg.text);
        }
      });
      res.on("end", retry);
      res.on("error", retry);
    });
    req.on("error", retry);
  }
  let retrying = false;
  function retry() {
    if (retrying || quitting) return;
    retrying = true;
    // The backend went away. A scene it was driving has nobody behind it now.
    hide();
    if (win && !win.isDestroyed()) win.webContents.send("teach-control", "dismiss");
    setTimeout(() => { retrying = false; subscribe(); }, backoff);
    backoff = Math.min(backoff * 2, 15000);
  }

  // ------------------------------------------------------------------ renderer → main
  ipcMain.handle("teach-spec", () => SPEC);
  ipcMain.on("teach-event", (e, evt) => {
    if (!win || e.sender !== win.webContents || !evt || typeof evt !== "object") return;
    if (evt.type === "idle" && !pen) hide();
    // Only numbers and short fixed strings go on to the backend.
    const out = { type: String(evt.type).slice(0, 16) };
    for (const k of ["gen", "seq", "drift_ms", "cmd_ms", "e2e_ms", "objects", "failed", "avg_frame_ms", "max_frame_ms", "frames"]) {
      if (typeof evt[k] === "number" && Number.isFinite(evt[k])) out[k] = evt[k];
    }
    for (const k of ["lesson", "op"]) if (typeof evt[k] === "string") out[k] = evt[k].slice(0, 48);
    if (typeof evt.why === "string") out.why = evt.why.slice(0, 160);
    if (typeof evt.low === "boolean") out.low = evt.low;
    post("teach_event", out);
  });
  ipcMain.on("teach-pen", (e, on) => {
    if (win && e.sender === win.webContents) setPen(!!on, true);
  });

  // ------------------------------------------------------------------ shortcuts
  // GNOME on Wayland does not give X11 key grabs the keyboard while a native window has focus,
  // so the dependable way in is a GNOME keybinding that signals this process (SIGURG, whose
  // default action is to be ignored — nothing breaks if it reaches a process not listening) or
  // writes to the control socket. The Electron shortcuts are registered as well, for X11.
  process.on("SIGURG", () => dismiss("shortcut"));

  const sockPath = path.join(process.env.XDG_RUNTIME_DIR || app.getPath("temp"), "jarvis-teach.sock");
  try { fs.unlinkSync(sockPath); } catch { /* not there */ }
  const server = net.createServer((s) => {
    let data = "";
    s.setTimeout(1000, () => s.destroy());
    s.on("data", (d) => {
      data += d;
      if (data.length > 64) s.destroy();
    });
    s.on("end", () => {
      const word = data.trim();
      if (word === "dismiss") dismiss("shortcut");
      else if (["pen", "pen-on", "pen-off"].includes(word)) handleControl(JSON.stringify({ action: word }));
      s.end();
    });
    s.on("error", () => {});
  });
  server.on("error", (e) => say(`teach control socket: ${e.message}`));
  server.listen(sockPath, () => { try { fs.chmodSync(sockPath, 0o600); } catch { /* best effort */ } });

  const keys = { dismiss: "Control+Super+Escape", pen: "Control+Super+P", ...(shortcuts || {}) };
  try { globalShortcut.register(keys.dismiss, () => dismiss("shortcut")); } catch { /* taken */ }
  try { globalShortcut.register(keys.pen, () => setPen(!pen)); } catch { /* taken */ }

  // ------------------------------------------------------------------ display changes
  // A scene is laid out for one monitor at one scale. When that changes, it is cleared rather
  // than left floating somewhere it no longer belongs.
  const changed = () => {
    if (shown) {
      hide();
      if (win && !win.isDestroyed()) win.webContents.send("teach-control", "dismiss");
      post("teach_event", { type: "display_changed" });
    }
    post("teach_event", { type: "displays", displays: describeDisplays() });
    if (win && !win.isDestroyed()) place(screen.getPrimaryDisplay());
  };
  screen.on("display-added", changed);
  screen.on("display-removed", changed);
  screen.on("display-metrics-changed", (_e, d, metrics) => {
    if (metrics.includes("scaleFactor") || metrics.includes("bounds") || metrics.includes("rotation")) changed();
    else if (monitor && d.id === monitor.id && win && !win.isDestroyed()) place(d);   // the work area moved
  });

  create();
  subscribe();

  return {
    dismiss,
    setPen,
    stats,
    quit() {
      quitting = true;
      try { server.close(); fs.unlinkSync(sockPath); } catch { /* gone */ }
      if (win && !win.isDestroyed()) win.destroy();
    },
  };
}

module.exports = { setup };
