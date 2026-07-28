// JARVIS console — a compact, always-on-top, FULLY INTERACTIVE floating HUD (not fullscreen), so
// typing and buttons work like a normal window and the desktop stays usable around it.
const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");

app.commandLine.appendSwitch("ozone-platform", "x11");

let win = null;       // interactive console (bottom)
let ambient = null;   // fullscreen, ALWAYS click-through, decorative HUD
let visible = true;
const COMPACT = { w: 560, h: 120 };
const EXPANDED = { w: 560, h: 500 };

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
  if (visible) { win.show(); ambient && ambient.showInactive(); }
  else { win.hide(); ambient && ambient.hide(); }
}
function hideAll() { if (win) win.hide(); if (ambient) ambient.hide(); visible = false; }
process.on("SIGUSR2", toggleOverlay);

function createWindow() {
  const a = screen.getPrimaryDisplay().workArea;
  win = new BrowserWindow({
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
    p.on("error", () => win && win.webContents.send("toast", "Install scrcpy + connect phone via USB"));
    p.unref();
    win && win.webContents.send("toast", "Opening phone…");
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
  } catch (e) {}
}

app.whenReady().then(() => {
  createAmbient();
  createWindow();
  try { fs.writeFileSync("/tmp/jarvis-overlay.pid", String(process.pid)); } catch (e) {}
  globalShortcut.register("Control+Super", toggleOverlay);
  globalShortcut.register("Control+Alt+J", toggleOverlay);
  globalShortcut.register("Control+Super+H", hideAll);
  watchScreencast();
  app.on("activate", () => BrowserWindow.getAllWindows().length === 0 && createWindow());
});

app.on("will-quit", () => globalShortcut.unregisterAll());
app.on("window-all-closed", () => app.quit());
