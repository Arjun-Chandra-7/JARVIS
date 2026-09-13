// Launch the real overlay in a throwaway X display and report what the renderer actually did.
//
// Screenshotting overlay/index.html in headless Chrome proves the design; it does not prove the
// app. Electron loads the page over file://, where Chromium refuses relative ES module imports
// for CORS reasons, and where the preload bridge and always-on-top window flags come into play.
// This runs the genuine main.js and prints every console message and page error the window
// produced, so a load failure is visible instead of silently degrading to a blank HUD.
//
//   xvfb-run -a node scripts/overlay-smoke.js [--shot out.png]
//
// JARVIS_OVERLAY_SPAWN=0 is forced: this must never start a second voice loop or backend.

process.env.JARVIS_OVERLAY_SPAWN = "0";

const path = require("path");
const fs = require("fs");
const { app, BrowserWindow } = require("electron");

const OVERLAY = path.resolve(__dirname, "..", "overlay");
const shotIdx = process.argv.indexOf("--shot");
const SHOT = shotIdx > -1 ? process.argv[shotIdx + 1] : null;
const HOLD_MS = Number(process.env.SMOKE_HOLD_MS || 5000);

const problems = [];

app.commandLine.appendSwitch("ozone-platform", "x11");

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 620, height: 520, show: true,
    transparent: true, frame: false, backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(OVERLAY, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.webContents.on("console-message", (_e, level, message, line, source) => {
    const where = source ? `${path.basename(source)}:${line}` : "";
    const tag = ["verbose", "info", "warning", "error"][level] || level;
    console.log(`[${tag}] ${message}  ${where}`);
    if (tag === "error") problems.push(message);
  });
  win.webContents.on("preload-error", (_e, p, err) => problems.push(`preload ${p}: ${err}`));
  win.webContents.on("did-fail-load", (_e, code, desc) => problems.push(`load failed ${code} ${desc}`));

  await win.loadFile(path.join(OVERLAY, "index.html"));
  await new Promise((r) => setTimeout(r, HOLD_MS));

  // Ask the page what actually came up. If the module graph failed, __hud is undefined and the
  // HUD is an inert shell — which is exactly the failure this script exists to catch.
  const report = await win.webContents.executeJavaScript(`(() => {
    const q = (s) => document.querySelector(s);
    const canvas = document.getElementById('reactorGl');
    let glOk = false;
    try { glOk = !!(canvas && canvas.getContext('webgl2')); } catch (e) {}
    return {
      hudExposed: typeof window.__hud === 'object' && window.__hud !== null,
      reactorAlive: !!(window.__hud && window.__hud.reactor && !window.__hud.reactor.unsupported),
      webgl2: glOk,
      bridge: typeof window.jarvis === 'object',
      bodyClass: document.body.className,
      status: (q('#status') || {}).textContent || '',
      statusWidth: q('#status') ? Math.round(q('#status').getBoundingClientRect().width) : -1,
      barHeight: q('.bar') ? Math.round(q('.bar').getBoundingClientRect().height) : -1,
      inputPresent: !!q('#msg'),
      popoverSupported: typeof HTMLElement.prototype.showPopover === 'function',
      viewTransitions: typeof document.startViewTransition === 'function',
      chrome: navigator.userAgent.match(/Chrome\\/([0-9.]+)/)?.[1] || '?',
    };
  })()`);

  console.log("\n--- overlay smoke report ---");
  for (const [k, v] of Object.entries(report)) console.log(`  ${k}: ${v}`);

  if (SHOT) {
    const img = await win.webContents.capturePage();
    fs.writeFileSync(SHOT, img.toPNG());
    console.log(`  screenshot: ${SHOT}`);
  }

  const fatal = [];
  if (!report.hudExposed) fatal.push("renderer.js did not finish — the module graph failed to load");
  if (!report.bridge) fatal.push("preload bridge missing");
  if (report.statusWidth === 0) fatal.push("status line collapsed to zero width");
  if (report.barHeight < 50) fatal.push("console bar did not lay out");

  if (problems.length) {
    console.log("\n  console errors:");
    for (const p of problems.slice(0, 12)) console.log("   -", p);
  }
  if (fatal.length) {
    console.log("\nFAIL:");
    for (const f of fatal) console.log("   -", f);
  } else {
    console.log("\nPASS: overlay loaded and laid out");
  }
  app.exit(fatal.length ? 1 : 0);
});

app.on("window-all-closed", () => app.quit());
