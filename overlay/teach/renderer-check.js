// Behaviour checks for the renderer, run against the real teach.html in an offscreen window.
//
//   electron teach/renderer-check.js     → prints one JSON object of results
//
// Used by tests/test_teach_renderer.py. The DOM is read back with executeJavaScript, which only
// this harness can do — the production window has no such door.
"use strict";

const { app, BrowserWindow, ipcMain } = require("electron");
const fs = require("fs");
const os = require("os");
const path = require("path");

app.setPath("userData", path.join(os.tmpdir(), "jarvis-teach-check"));
app.commandLine.appendSwitch("ozone-platform", "x11");
const SPEC = JSON.parse(fs.readFileSync(path.join(__dirname, "protocol.json"), "utf8"));
const events = [];
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

app.whenReady().then(async () => {
  ipcMain.handle("teach-spec", () => SPEC);
  ipcMain.on("teach-event", (_e, evt) => events.push(evt));
  ipcMain.on("teach-pen", () => {});
  const win = new BrowserWindow({
    width: 1280, height: 720, show: false, transparent: true, frame: false,
    webPreferences: { preload: path.join(__dirname, "..", "teach-preload.js"), sandbox: true, contextIsolation: true, offscreen: true },
  });
  await win.loadFile(path.join(__dirname, "..", "teach.html"));
  await wait(300);
  win.webContents.send("teach-geometry", { offset: { x: 0, y: 0 }, w: 1280, h: 720, scale: 1 });
  let seq = 0;
  const send = (gen, cmds, extra) => win.webContents.send("teach-batch", { v: 1, lesson: "t", gen, seq: ++seq, cmds, ...(extra || {}) }, Date.now());
  const dom = (js) => win.webContents.executeJavaScript(js);
  const count = () => dom("document.querySelectorAll('#world [data-id]').length");
  const R = {};

  const circle = (id, extra) => ({ op: "shape.add", object: { id, type: "circle", cx: 100, cy: 100, r: 30, ...(extra || {}) } });

  // 1. draw, then a stale generation cannot add anything
  send(10, [circle("a")]);
  await wait(80);
  R.drawn = await count();
  send(9, [circle("old")]);
  await wait(80);
  R.after_stale = await count();
  R.stale_reported = events.some((e) => e.type === "stale");

  // 2. a batch carrying script is refused whole, by the renderer's own validator
  send(10, [{ op: "shape.add", object: { id: "x", type: "text", x: 1, y: 1, text: "<img src=x onerror=alert(1)>" } }]);
  send(10, [{ op: "eval", code: "1" }]);
  await wait(80);
  R.rejected = events.filter((e) => e.type === "rejected").length;
  R.after_bad = await count();

  // 3. a scheduled batch from an old generation never lands once a newer one arrives
  send(11, [circle("later")], { at: Date.now() + 300 });
  send(12, [circle("now")]);
  await wait(450);
  R.later_landed = await dom("!!document.querySelector('[data-id=later]')");

  // 4. cancelling a timeline removes what it had half-drawn
  send(12, [circle("slow", { anim: { kind: "draw", ms: 5000, timeline: "s1" } })]);
  await wait(60);
  send(12, [{ op: "timeline.cancel", timeline: "s1" }]);
  await wait(60);
  R.cancelled_gone = await dom("!document.querySelector('[data-id=slow]')");

  // 5. pause freezes progress
  send(12, [circle("p", { anim: { kind: "fade", ms: 600, timeline: "s2" } })]);
  await wait(100);
  send(12, [{ op: "timeline.pause" }]);
  const o1 = await dom("getComputedStyle(document.querySelector('[data-id=p] g')).opacity");
  await wait(300);
  const o2 = await dom("getComputedStyle(document.querySelector('[data-id=p] g')).opacity");
  R.paused_holds = Math.abs(Number(o1) - Number(o2)) < 0.02;
  send(12, [{ op: "timeline.resume" }]);

  // 6. anchors: follow, then hide when stale
  send(12, [{ op: "anchor.attach", anchor: { id: "v", source: "region", bounds: { x: 0, y: 0, w: 400, h: 300 }, confidence: 0.9,
                                              observed_at: Date.now(), ttl_ms: 400, monitor: 0, scale: 1, tracking: "follow" } },
            circle("anc", { anchor: "v" })]);
  await wait(60);
  send(12, [{ op: "anchor.attach", anchor: { id: "v", source: "region", bounds: { x: 50, y: 20, w: 400, h: 300 }, confidence: 0.9,
                                              observed_at: Date.now(), ttl_ms: 400, monitor: 0, scale: 1, tracking: "follow" } }]);
  await wait(60);
  R.anchor_moved = await dom("document.querySelector('[data-id=anc]').getAttribute('transform')");
  await wait(700);
  R.anchor_stale_hidden = await dom("document.querySelector('[data-id=anc]').classList.contains('hidden')");

  // 7. object limit
  const many = [];
  for (let i = 0; i < 150; i++) many.push(circle(`m${i}`));
  send(12, many); send(12, many.map((c) => ({ ...c, object: { ...c.object, id: c.object.id + "b" } })));
  send(12, many.map((c) => ({ ...c, object: { ...c.object, id: c.object.id + "c" } })));
  await wait(200);
  R.objects_capped = await count();
  R.limit_errors = events.filter((e) => e.type === "error" && /too many objects/.test(e.why || "")).length;

  // 8. emergency dismissal empties everything at once
  const t0 = Date.now();
  win.webContents.send("teach-control", "dismiss");
  await wait(20);
  R.after_dismiss = await count();
  R.dismiss_ms = Date.now() - t0;

  // 9. reduced motion: objects appear finished, no animation loop
  send(13, [{ op: "scene.create", scene: "r", monitor: 0, theme: { reduced_motion: true } },
            circle("rm", { anim: { kind: "draw", ms: 3000 } })]);
  await wait(60);
  R.reduced_no_dash = await dom("!document.querySelector('[data-id=rm] .core').style.strokeDasharray");

  // 10. an idle scene says so (the main process hides the window on it)
  send(14, [{ op: "scene.clear" }]);
  await wait(300);
  R.idle_reported = events.some((e) => e.type === "idle");
  R.frames = events.filter((e) => e.type === "frame").length;
  process.stdout.write(JSON.stringify(R) + "\n");
  app.quit();
});
