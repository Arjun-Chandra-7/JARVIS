// JARVIS console — a compact, always-on-top, FULLY INTERACTIVE floating HUD (not fullscreen), so
// typing and buttons work like a normal window and the desktop stays usable around it.
// The overlay also *is* Jarvis: it auto-starts the backend (:8770) and the always-listening
// "Hey Jarvis" voice loop, so launching the overlay = a live, listening assistant.
const { app, BrowserWindow, globalShortcut, ipcMain, screen, session } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

app.commandLine.appendSwitch("ozone-platform", "x11");
// Grant camera/mic access automatically (frameless windows can't show permission prompts)
app.commandLine.appendSwitch("enable-features", "WebRTCPipeWireCapturer");

const REPO = path.resolve(__dirname, "..");
const PY = path.join(REPO, ".venv", "bin", "python");
const PORT = process.env.JARVIS_WEB_PORT || "8770";

let win = null;       // interactive console (bottom)
let ambient = null;   // fullscreen, ALWAYS click-through, decorative HUD
let visible = true;
const COMPACT = { w: 620, h: 128 };
const EXPANDED = { w: 620, h: 520 };

// --------------------------------------------------------------------------- //
// backend + voice as managed child processes (always-listening)               //
// --------------------------------------------------------------------------- //
const kids = [];
function cleanEnv() {
  const env = { ...process.env };
  delete env.LD_LIBRARY_PATH; delete env.LD_PRELOAD;   // drop snap/conda pollution
  env.JARVIS_WEB_PORT = PORT;
  return env;
}
function probe(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/stats", timeout: 800 }, (r) => { r.destroy(); resolve(true); });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}
function supervise(name, args, logfile) {
  let stopped = false, backoff = 1000;
  const start = () => {
    const out = fs.openSync(logfile, "a");
    const p = spawn(PY, ["-m", "jarvis", ...args], { cwd: REPO, env: cleanEnv(), stdio: ["ignore", out, out] });
    kids.push(p);
    p.on("exit", () => {
      if (stopped || app.isQuiting) return;
      setTimeout(start, backoff);
      backoff = Math.min(backoff * 2, 15000);
    });
    p.on("spawn", () => { backoff = 1000; });
  };
  start();
  return () => { stopped = true; };
}
async function ensureBackend() {
  if (!fs.existsSync(PY)) { toast("No .venv — run pip install -r requirements.txt"); return; }
  if (process.env.JARVIS_OVERLAY_SPAWN === "0") return;   // external launcher owns the processes
  const up = await probe(PORT);
  if (!up) supervise("web", ["--web"], "/tmp/jarvis-web.log");
  // always-listening wake word; the voice loop pushes events to the HUD via /emit
  supervise("voice", ["--voice"], "/tmp/jarvis-voice.log");
}

function toast(msg) { win && win.webContents.send("toast", msg); }

function createAmbient() {
  const a = screen.getPrimaryDisplay().workArea;
  ambient = new BrowserWindow({
    x: a.x, y: a.y, width: a.width, height: a.height,
    transparent: true, frame: false, resizable: false, movable: false, skipTaskbar: true,
    hasShadow: false, focusable: false, fullscreenable: false, backgroundColor: "#00000000",
    webPreferences: { preload: `${__dirname}/preload.js`, contextIsolation: true, nodeIntegration: false },
  });
  ambient.setAlwaysOnTop(true, "screen-saver");
  ambient.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  ambient.setIgnoreMouseEvents(true); // permanent → never steals a click, so it can't break anything
  ambient.loadFile("ambient.html");
}

function place(size) {
  if (!win) return;
  const a = screen.getPrimaryDisplay().workArea;
  const x = Math.round(a.x + (a.width - size.w) / 2);
  const y = Math.round(a.y + a.height - size.h - 26); // anchored near the bottom
  win.setBounds({ x, y, width: size.w, height: size.h });
}

function toggleOverlay() {
  if (!win) return;
  visible = !visible;
  if (visible) { win.show(); ambient && ambient.showInactive(); spWin && spWin.showInactive(); }
  else { win.hide(); ambient && ambient.hide(); spWin && spWin.hide(); }
}
function hideAll() { if (win) win.hide(); if (ambient) ambient.hide(); if (spWin) spWin.hide(); if (crWin) crWin.hide(); visible = false; }
process.on("SIGUSR2", toggleOverlay);


let spWin = null;
function createSpotify() {
  const a = screen.getPrimaryDisplay().workArea;
  const w = 340, h = 100;
  spWin = new BrowserWindow({
    width: w, height: h,
    x: a.x + a.width - w - 40,
    y: a.y + a.height - h - 40,
    transparent: true, frame: false, resizable: false, movable: true, skipTaskbar: true,
    hasShadow: false, fullscreenable: false, focusable: false, backgroundColor: "#00000000",
    webPreferences: { contextIsolation: false, nodeIntegration: true },
  });
  spWin.setAlwaysOnTop(true, "screen-saver");
  spWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  spWin.loadFile("spotify.html");
}


let crWin = null;
function createCricket() {
  const a = screen.getPrimaryDisplay().workArea;
  const w = 340, h = 180;
  crWin = new BrowserWindow({
    width: w, height: h,
    x: a.x + 40,
    y: a.y + a.height - h - 145,
    transparent: true, frame: false, resizable: false, movable: true, skipTaskbar: true,
    hasShadow: false, fullscreenable: false, focusable: false, backgroundColor: "#00000000",
    webPreferences: { contextIsolation: false, nodeIntegration: true },
    show: false
  });
  crWin.setAlwaysOnTop(true, "screen-saver");
  crWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  crWin.loadFile("cricket.html");
}

ipcMain.on("sports_toggle", (_e, state) => {
  if (state === "on") crWin && crWin.showInactive();
  else if (state === "off") crWin && crWin.hide();
  else {
    if (crWin && crWin.isVisible()) crWin.hide();
    else if (crWin) crWin.showInactive();
  }
});

function createWindow() {
  const a = screen.getPrimaryDisplay().workArea;
  win = new BrowserWindow({
    icon: path.join(__dirname, "icon.png"),
    width: COMPACT.w, height: COMPACT.h,
    x: Math.round(a.x + (a.width - COMPACT.w) / 2),
    y: Math.round(a.y + a.height - COMPACT.h - 26),
    transparent: true, frame: false, resizable: false, movable: true, skipTaskbar: true,
    hasShadow: false, fullscreenable: false, focusable: true, backgroundColor: "#00000000",
    webPreferences: { preload: `${__dirname}/preload.js`, contextIsolation: true, nodeIntegration: false },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.loadFile("index.html");
}

ipcMain.on("mode", (_e, mode) => place(mode === "text" ? EXPANDED : COMPACT));

// pinch-zoom → GNOME magnifier
let lastZoom = 1;
ipcMain.on("set-zoom", (_e, factor) => {
  const f = Math.max(1, Math.min(5, factor));
  if (Math.abs(f - lastZoom) < 0.08) return;
  lastZoom = f;
  const run = (s, k, v) => spawn("gsettings", ["set", s, k, String(v)], { stdio: "ignore" });
  if (f <= 1.05) run("org.gnome.desktop.a11y.applications", "screen-magnifier-enabled", "false");
  else {
    run("org.gnome.desktop.a11y.magnifier", "mouse-tracking", "proportional");
    run("org.gnome.desktop.a11y.applications", "screen-magnifier-enabled", "true");
    run("org.gnome.desktop.a11y.magnifier", "mag-factor", f.toFixed(2));
  }
});

ipcMain.on("launch-phone", () => {
  const a = screen.getPrimaryDisplay().workArea;
  const w = 340, x = Math.round(a.x + a.width / 2 - w / 2), y = Math.round(a.y + a.height * 0.08);
  try {
    const p = spawn("scrcpy", ["--window-title=JARVIS Phone", "--window-borderless", "--always-on-top",
      `--window-x=${x}`, `--window-y=${y}`, `--window-width=${w}`, "--stay-awake"], { detached: true, stdio: "ignore" });
    p.on("error", () => toast("Install scrcpy + connect phone via USB"));
    p.unref();
    toast("Opening phone…");
  } catch (e) {}
});

ipcMain.on("quit", () => app.quit());

// PRIVACY: auto-hide while screen-sharing (gmeet/discord use the ScreenCast portal)
function watchScreencast() {
  try {
    const mon = spawn("dbus-monitor", ["--session", "interface='org.freedesktop.portal.ScreenCast'"],
      { stdio: ["ignore", "pipe", "ignore"] });
    mon.stdout.on("data", (d) => {
      if (/member=(Start|SelectSources|CreateSession)/.test(d.toString()) && visible) hideAll();
    });
    kids.push(mon);
  } catch (e) {}
}

app.whenReady().then(() => {
  // Auto-grant camera + mic — frameless overlay windows can't show permission dialogs
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    const allowed = ["media", "mediaKeySystem", "display-capture", "accessibility-events"];
    callback(allowed.includes(permission));
  });
  session.defaultSession.setPermissionCheckHandler((webContents, permission) => {
    return ["media", "mediaKeySystem", "display-capture"].includes(permission);
  });

  createAmbient();
  createWindow();
  createSpotify();
  createCricket();
  ensureBackend();
  try { fs.writeFileSync("/tmp/jarvis-overlay.pid", String(process.pid)); } catch (e) {}
  try { globalShortcut.register("Control+Super+Space", toggleOverlay); } catch (e) {}
  try { globalShortcut.register("Control+Alt+J", toggleOverlay); } catch (e) {}
  try { globalShortcut.register("Control+Super+H", hideAll); } catch (e) {}
  watchScreencast();
  app.on("activate", () => BrowserWindow.getAllWindows().length === 0 && createWindow());
});

app.on("will-quit", () => {
  app.isQuiting = true;
  globalShortcut.unregisterAll();
  for (const k of kids) { try { k.kill("SIGTERM"); } catch (e) {} }
});
app.on("window-all-closed", () => app.quit());
