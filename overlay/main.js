// JARVIS overlay — one window that changes shape, not four windows fighting for the screen.
//
// The previous overlay opened four always-on-top windows (a fullscreen ambient HUD, the console,
// a Spotify widget and a cricket widget). The fullscreen layer sat on top of real work — in the
// baseline capture its subsystem panel covered the VS Code file tree — and none of them shared
// state. This is a single window in three forms:
//
//   pill          a small presence chip: mic state, activity, nothing else
//   conversation  live transcript, input, audio-reactive feedback, stop/cancel
//   workspace     resizable: conversation, task steps, result cards, memory, diagnostics
//
// Position and size are remembered per form, the invocation shortcut is configurable, and the
// window opens on whichever display the pointer is on.
const { app, BrowserWindow, globalShortcut, ipcMain, screen, session, shell } = require("electron");
const { spawn, spawnSync } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

// GNOME's Wayland compositor gives Electron no way to stay reliably on top or position itself, so
// the overlay runs through XWayland. This is a deliberate platform trade-off, not an oversight.
app.commandLine.appendSwitch("ozone-platform", "x11");
app.commandLine.appendSwitch("enable-features", "WebRTCPipeWireCapturer");

const REPO = path.resolve(__dirname, "..");
const PY = path.join(REPO, ".venv", "bin", "python");
const PORT = process.env.JARVIS_WEB_PORT || "8770";

const CONFIG_DIR = path.join(app.getPath("home"), ".config", "jarvis");
const STATE_FILE = path.join(CONFIG_DIR, "overlay-state.json");

// Minimum sizes stop a remembered size from making a form unusable.
const FORMS = {
  pill: { w: 280, h: 60, minW: 200, minH: 52, resizable: false },
  conversation: { w: 660, h: 440, minW: 460, minH: 300, resizable: true },
  workspace: { w: 1000, h: 680, minW: 680, minH: 420, resizable: true },
};

const DEFAULT_SHORTCUTS = {
  toggle: "Control+Super+Space",
  workspace: "Control+Super+J",
  hide: "Control+Super+H",
};

let win = null;

// The pill grows to show an answer and shrinks back when it has gone. It grows upward — the
// bottom edge stays put — because the pill lives near the bottom of the screen and an edge that
// moves is an edge you have to look for. The bounds from before the growth are kept here and
// restored exactly, and none of it is written to disk: a temporary height saved as the remembered
// one would leave the pill a little taller after every answer.
// Declared beside the window it belongs to, because applyForm reads it and is defined far above
// the handler that sets it.
let pillRestore = null;
let form = "pill";
let visible = true;
let state = { bounds: {}, shortcuts: {}, form: "pill" };

// --------------------------------------------------------------------------- persisted state
function loadState() {
  try {
    const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    state = { bounds: {}, shortcuts: {}, form: "pill", ...raw };
  } catch {
    /* first run */
  }
  state.shortcuts = { ...DEFAULT_SHORTCUTS, ...(state.shortcuts || {}) };
}

let saveTimer = null;
function saveState() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      fs.mkdirSync(CONFIG_DIR, { recursive: true });
      fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
    } catch {
      /* losing a window position is not worth surfacing */
    }
  }, 400);
}

function rememberBounds() {
  if (!win || win.isDestroyed() || !win.isVisible()) return;
  const b = win.getBounds();
  // Only remember a position the user could actually have chosen.
  if (b.width > 0 && b.height > 0) state.bounds[form] = b;
  saveState();
}

// --------------------------------------------------------------------------- placement
function activeDisplay() {
  // Follow the pointer, so invoking on a second monitor opens there rather than on the primary.
  try {
    return screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  } catch {
    return screen.getPrimaryDisplay();
  }
}

function clampToDisplay(bounds) {
  const area = screen.getDisplayMatching(bounds).workArea;
  const w = Math.min(bounds.width, area.width);
  const h = Math.min(bounds.height, area.height);
  return {
    width: w,
    height: h,
    x: Math.round(Math.min(Math.max(bounds.x, area.x), area.x + area.width - w)),
    y: Math.round(Math.min(Math.max(bounds.y, area.y), area.y + area.height - h)),
  };
}

function defaultBounds(name) {
  const spec = FORMS[name];
  const area = activeDisplay().workArea;
  return {
    width: spec.w,
    height: spec.h,
    x: Math.round(area.x + (area.width - spec.w) / 2),
    y: Math.round(area.y + area.height - spec.h - 40),
  };
}

function applyForm(name, { animate = true } = {}) {
  if (!win || !FORMS[name] || win.isDestroyed()) return;
  // Drop any temporary growth before measuring, so an answer showing at the moment someone
  // expands the pill cannot be remembered as the pill's real height.
  if (pillRestore) {
    win.setBounds(clampToDisplay(pillRestore));
    pillRestore = null;
  }
  if (name !== form) rememberBounds();
  form = name;
  state.form = name;

  const spec = FORMS[name];
  const remembered = state.bounds[name];
  let target = remembered ? clampToDisplay(remembered) : defaultBounds(name);

  // A remembered size smaller than the form can use would clip its content.
  target.width = Math.max(target.width, spec.minW);
  target.height = Math.max(target.height, spec.minH);
  target = clampToDisplay(target);

  win.setMinimumSize(spec.minW, spec.minH);
  win.setResizable(spec.resizable);
  win.setBounds(target, animate && process.platform === "darwin");
  win.webContents.send("form", name);
  saveState();
}

// --------------------------------------------------------------------------- backend
const kids = [];
function cleanEnv() {
  const env = { ...process.env };
  delete env.LD_LIBRARY_PATH;
  delete env.LD_PRELOAD;
  env.JARVIS_WEB_PORT = PORT;
  return env;
}

function probe(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/stats", timeout: 800 }, (r) => {
      r.destroy();
      resolve(true);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

function supervise(args, logfile) {
  let backoff = 1000;
  const start = () => {
    const out = fs.openSync(logfile, "a");
    const p = spawn(PY, ["-m", "jarvis", ...args], {
      cwd: REPO, env: cleanEnv(), stdio: ["ignore", out, out],
    });
    kids.push(p);
    p.on("exit", () => {
      if (app.isQuiting) return;
      setTimeout(start, backoff);
      backoff = Math.min(backoff * 2, 15000);
    });
    p.on("spawn", () => { backoff = 1000; });
  };
  start();
}

function voiceRunning() {
  // systemd owns jarvis-voice in the normal install. Spawning a second voice loop would put two
  // processes on one microphone, and the second one silently loses.
  return new Promise((resolve) => {
    const p = spawn("systemctl", ["--user", "is-active", "jarvis-voice.service"], {
      stdio: ["ignore", "pipe", "ignore"],
    });
    let out = "";
    p.stdout.on("data", (d) => (out += d));
    p.on("close", () => resolve(out.trim() === "active"));
    p.on("error", () => resolve(false));
  });
}

async function ensureBackend() {
  if (process.env.JARVIS_OVERLAY_SPAWN === "0") return;
  if (!fs.existsSync(PY)) {
    toast("No .venv — run pip install -r requirements.txt");
    return;
  }
  if (!(await probe(PORT))) supervise(["--web"], "/tmp/jarvis-web.log");
  if (!(await voiceRunning())) supervise(["--voice"], "/tmp/jarvis-voice.log");
}

function toast(msg) {
  if (win && !win.isDestroyed()) win.webContents.send("toast", msg);
}

// --------------------------------------------------------------------------- window
function createWindow() {
  const bounds = state.bounds[state.form] ? clampToDisplay(state.bounds[state.form])
                                          : defaultBounds(state.form);
  form = state.form in FORMS ? state.form : "pill";

  win = new BrowserWindow({
    ...bounds,
    icon: path.join(__dirname, "icon.png"),
    transparent: true,
    frame: false,
    resizable: FORMS[form].resizable,
    movable: true,
    skipTaskbar: true,
    hasShadow: false,
    fullscreenable: false,
    // focusable so the text input and keyboard navigation work like a normal window; the window
    // is shown without activation, so appearing never steals focus from what you were typing in.
    focusable: true,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
    },
  });

  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.loadFile("index.html");
  win.on("resize", rememberBounds);
  win.on("move", rememberBounds);
  win.once("ready-to-show", () => {
    win.showInactive();
    win.webContents.send("form", form);
  });

  // Anything the page tries to open goes to the real browser, never inside the overlay.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith("file://")) e.preventDefault();
  });
}

function show({ focus = false, to = null } = {}) {
  if (!win || win.isDestroyed()) return;
  if (to) applyForm(to);
  visible = true;
  if (focus) {
    win.show();
    win.focus();
  } else {
    win.showInactive();
  }
  win.webContents.send("visibility", true);
}

function hide() {
  if (!win || win.isDestroyed()) return;
  rememberBounds();
  visible = false;
  win.hide();
  win.webContents.send("visibility", false); // lets the renderer stop its animation loops
}

function toggle() {
  if (visible && win && win.isVisible()) hide();
  else show({ focus: true, to: form === "pill" ? "conversation" : form });
}

process.on("SIGUSR2", toggle);

// --------------------------------------------------------------------------- IPC (narrow)
ipcMain.on("form", (_e, name) => {
  if (FORMS[name]) applyForm(name);
});
ipcMain.on("grow-pill", (_e, extra) => {
  if (!win || win.isDestroyed() || form !== "pill") return;
  const px = Math.max(0, Math.min(160, Math.round(Number(extra) || 0)));

  if (px === 0) {
    if (pillRestore) win.setBounds(clampToDisplay(pillRestore));
    pillRestore = null;
    return;
  }
  const base = pillRestore || win.getBounds();
  pillRestore = base;
  const height = base.height + px;
  win.setBounds(clampToDisplay({ ...base, height, y: base.y + base.height - height }));
});

ipcMain.on("hide", hide);
ipcMain.on("focus-window", () => {
  if (win && !win.isDestroyed()) win.focus();
});
ipcMain.on("quit", () => app.quit());
ipcMain.handle("get-state", () => ({
  form,
  shortcuts: state.shortcuts,
  port: PORT,
  reduceMotion: !!state.reduceMotion,
}));
ipcMain.handle("set-shortcut", (_e, which, accelerator) => {
  if (!DEFAULT_SHORTCUTS[which]) return { ok: false, error: "unknown shortcut" };
  const previous = state.shortcuts[which];
  try {
    if (previous) globalShortcut.unregister(previous);
    if (accelerator && !globalShortcut.register(accelerator, handlerFor(which))) {
      // Re-register the old one so a rejected accelerator does not leave the user with none.
      if (previous) globalShortcut.register(previous, handlerFor(which));
      return { ok: false, error: "the system refused that combination" };
    }
    state.shortcuts[which] = accelerator;
    saveState();
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
});

ipcMain.on("launch-phone", () => {
  const area = activeDisplay().workArea;
  const w = 340;
  const x = Math.round(area.x + area.width / 2 - w / 2);
  const y = Math.round(area.y + area.height * 0.08);
  try {
    const p = spawn("scrcpy", ["--window-title=JARVIS Phone", "--window-borderless",
      "--always-on-top", `--window-x=${x}`, `--window-y=${y}`, `--window-width=${w}`,
      "--stay-awake"], { detached: true, stdio: "ignore" });
    p.on("error", () => toast("Install scrcpy and connect the phone over USB"));
    p.unref();
    toast("Opening phone…");
  } catch {
    toast("Could not start scrcpy");
  }
});

ipcMain.on("open-external", (_e, url) => {
  if (typeof url === "string" && /^https?:\/\//.test(url)) shell.openExternal(url);
});

function handlerFor(which) {
  if (which === "toggle") return toggle;
  if (which === "workspace") return () => show({ focus: true, to: "workspace" });
  return hide;
}

function registerShortcuts() {
  for (const which of Object.keys(DEFAULT_SHORTCUTS)) {
    const accel = state.shortcuts[which];
    if (!accel) continue;
    try {
      if (!globalShortcut.register(accel, handlerFor(which))) {
        // Another application already owns it. Say so rather than failing silently.
        setTimeout(() => toast(`Shortcut ${accel} is already taken`), 2500);
      }
    } catch {
      /* malformed accelerator in the state file */
    }
  }
}

// PRIVACY: get out of the way when a screen share starts (Meet/Discord use the ScreenCast portal).
function watchScreencast() {
  try {
    // A dbus-monitor spawned by a previous overlay survives if that overlay was killed rather
    // than asked to quit — will-quit never runs, so the child is orphaned. They accumulate one
    // per launch and hold inherited sockets, so clear any stale ones before starting another.
    try {
      // Synchronous: an async pkill races the spawn below and can kill the monitor we just made.
      spawnSync("pkill",
        ["-f", "dbus-monitor --session interface='org.freedesktop.portal.ScreenCast'"],
        { stdio: "ignore", timeout: 2000 });
    } catch { /* pkill missing: at worst one monitor lingers */ }

    const mon = spawn("dbus-monitor",
      ["--session", "interface='org.freedesktop.portal.ScreenCast'"],
      { stdio: ["ignore", "pipe", "ignore"] });
    mon.stdout.on("data", (d) => {
      if (/member=(Start|SelectSources|CreateSession)/.test(d.toString()) && visible) hide();
    });
    kids.push(mon);
  } catch {
    /* no dbus-monitor: the overlay simply stays visible */
  }
}

// Only one Jarvis. Without this, starting it a second time — the launcher clicked twice, the
// service running alongside a manual start — silently brings up a whole second copy: a second
// window, a second compositor process, a second websocket to the backend, and two of every poll
// loop. It was measured happening on this machine: two overlays, 27% of a core each.
//
// Nothing warns you, because both copies work. The second start now hands its request to the
// copy already running and gets out of the way, which is also what a person clicking the
// launcher again actually wants: show me Jarvis.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!win || win.isDestroyed()) return;
    if (win.isMinimized()) win.restore();
    show({ focus: true });
  });
}

app.whenReady().then(() => {
  loadState();

  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(["media", "mediaKeySystem", "display-capture"].includes(permission));
  });
  session.defaultSession.setPermissionCheckHandler((_wc, permission) =>
    ["media", "mediaKeySystem", "display-capture"].includes(permission));

  createWindow();
  ensureBackend();
  registerShortcuts();
  watchScreencast();

  try {
    fs.writeFileSync("/tmp/jarvis-overlay.pid", String(process.pid));
  } catch {
    /* pid file is a convenience for scripts/stop.sh */
  }

  app.on("activate", () => BrowserWindow.getAllWindows().length === 0 && createWindow());
});

app.on("will-quit", () => {
  app.isQuiting = true;
  rememberBounds();
  globalShortcut.unregisterAll();
  for (const k of kids) {
    try { k.kill("SIGTERM"); } catch { /* already gone */ }
  }
});
app.on("window-all-closed", () => app.quit());
