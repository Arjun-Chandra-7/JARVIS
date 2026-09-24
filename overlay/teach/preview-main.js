// Visual check for the teaching layer: renders scenes through the real teach.html and preload,
// at a chosen size and device scale, and writes one transparent PNG per scene.
//
//   node_modules/.bin/electron teach/preview-main.js <scenes.json> <outdir> [--scale=1.25] [--size=1920x1080] [--top=28]
//
// scenes.json: [{ "name": "...", "batches": [{ "wait": ms, "batch": {...} }], "settle": ms }]
// Nothing here touches the running overlay: its own user-data directory, no single-instance lock.
"use strict";

const { app, BrowserWindow, ipcMain } = require("electron");
const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2).filter((a) => !a.startsWith("--") && !a.endsWith("preview-main.js"));
const opt = Object.fromEntries(process.argv.filter((a) => a.startsWith("--") && a.includes("="))
  .map((a) => a.slice(2).split("=")));
const [scenesFile, outDir] = args.slice(-2);
const scale = Number(opt.scale || 1);
const [W, H] = String(opt.size || "1920x1080").split("x").map(Number);
const TOP = Number(opt.top === undefined ? 28 : opt.top);

app.setPath("userData", path.join(require("os").tmpdir(), "jarvis-teach-preview"));
app.commandLine.appendSwitch("force-device-scale-factor", String(scale));
app.commandLine.appendSwitch("ozone-platform", "x11");
app.commandLine.appendSwitch("enable-transparent-visuals");

const SPEC = JSON.parse(fs.readFileSync(path.join(__dirname, "protocol.json"), "utf8"));
const events = [];

app.whenReady().then(async () => {
  ipcMain.handle("teach-spec", () => SPEC);
  ipcMain.on("teach-event", (_e, evt) => events.push(evt));
  ipcMain.on("teach-pen", () => {});
  const win = new BrowserWindow({
    width: W, height: H - TOP, show: false, transparent: true, frame: false, backgroundColor: "#00000000",
    webPreferences: { preload: path.join(__dirname, "..", "teach-preload.js"), sandbox: true, contextIsolation: true,
                      offscreen: true },
  });
  win.webContents.setFrameRate(60);
  await win.loadFile(path.join(__dirname, "..", "teach.html"));
  await new Promise((r) => setTimeout(r, 300));
  win.webContents.send("teach-geometry", { offset: { x: 0, y: TOP }, w: W, h: H - TOP, scale });
  win.webContents.send("teach-control", "visible", true);
  const scenes = JSON.parse(fs.readFileSync(scenesFile, "utf8"));
  fs.mkdirSync(outDir, { recursive: true });
  for (const scene of scenes) {
    win.webContents.send("teach-control", "dismiss");
    await new Promise((r) => setTimeout(r, 60));
    for (const b of scene.batches) {
      if (b.wait) await new Promise((r) => setTimeout(r, b.wait));
      const now = Date.now();
      const batch = { ...b.batch, sent: now };
      win.webContents.send("teach-batch", batch, now);
    }
    await new Promise((r) => setTimeout(r, scene.settle || 1500));
    const img = await win.webContents.capturePage();
    const file = path.join(outDir, `${scene.name}@${scale}x-${W}x${H}.png`);
    fs.writeFileSync(file, img.toPNG());
    console.log(JSON.stringify({ scene: scene.name, file, size: img.getSize() }));
  }
  const errors = events.filter((e) => ["error", "rejected", "stale"].includes(e.type));
  const frames = events.filter((e) => e.type === "frame");
  console.log(JSON.stringify({ errors, frames: frames.length,
                               cmd_ms: frames.map((f) => f.cmd_ms).filter((x) => x !== null) }));
  app.quit();
});
