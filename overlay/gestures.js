// Hand tracking (MediaPipe): pinch-HOLD to zoom the desktop, twirl-to-charge a Rasengan, thrust to release.
import { HandLandmarker, FilesetResolver } from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";

const video = document.getElementById("cam");
const canvas = document.getElementById("handfx");
const ctx = canvas.getContext("2d");
const hint = document.getElementById("ghint");

let landmarker = null, lastResult = null, lastVideoTime = -1, prevT = performance.now();

async function init() {
  try {
    const vision = await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm");
    landmarker = await HandLandmarker.createFromOptions(vision, {
      baseOptions: { modelAssetPath: "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task" },
      numHands: 1,
      runningMode: "VIDEO",
      minHandDetectionConfidence: 0.7,   // strict → no phantom hands triggering things
      minHandPresenceConfidence: 0.7,
      minTrackingConfidence: 0.7,
    });
    requestAnimationFrame(loop);
  } catch (e) {
    console.log("hand tracking unavailable:", e);
    if (hint) { hint.textContent = "gestures offline"; hint.classList.add("show"); }
  }
}

// ---- helpers (normalized coords for logic; px for drawing) ----
const D = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const px = (lm) => (1 - lm.x) * canvas.width;   // video mirrored in CSS
const py = (lm) => lm.y * canvas.height;
const extended = (lm, tip, pip) => D(lm[tip], lm[0]) > D(lm[pip], lm[0]) * 1.08;
const pinchPose = (lm) => extended(lm, 8, 6) && !extended(lm, 12, 10) && !extended(lm, 16, 14) && !extended(lm, 20, 18);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function fit() { const w = canvas.clientWidth, h = canvas.clientHeight; if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; } }

// ---- state ----
let pinchFrames = 0, zoomThrottle = 0;
let charge = 0, state = "idle", release = null;       // idle | charging | releasing
const posHist = [];   // {x,y} normalized palm centre
const sizeHist = [];  // {s,t} hand span over time

function loop(now) {
  requestAnimationFrame(loop);
  const dt = Math.min(0.05, (now - prevT) / 1000); prevT = now;
  if (!landmarker || video.readyState < 2) return;
  fit();
  if (video.currentTime !== lastVideoTime) {
    lastVideoTime = video.currentTime;
    try { lastResult = landmarker.detectForVideo(video, now); } catch (e) {}
  }
  const hands = (lastResult && lastResult.landmarks) || [];
  update(hands[0] || null, now, dt);
  render(now);
}

function update(lm, now, dt) {
  let label = "";

  // ---------- no hand → stand everything down (fixes runaway zoom) ----------
  if (!lm) {
    if (pinchFrames > 0) { pinchFrames = 0; sendZoom(1); }
    posHist.length = 0; sizeHist.length = 0;
    if (state === "charging") { charge = Math.max(0, charge - dt * 1.5); if (charge <= 0) state = "idle"; }
    setHint(""); return;
  }

  // ---------- pinch-HOLD to zoom ----------
  if (pinchPose(lm)) pinchFrames++; else pinchFrames = 0;
  if (pinchFrames >= 6) {
    const ratio = D(lm[4], lm[8]) / (D(lm[0], lm[9]) || 0.1);
    sendZoom(clamp(1 + (ratio - 0.25) * 3, 1, 4), now);
    label = "⊕ ZOOM";
  } else if (pinchFrames === 0) {
    sendZoom(1);                 // not pinching → magnifier off
  }

  // ---------- Rasengan: twirl to charge, thrust to release ----------
  const cx = (lm[0].x + lm[5].x + lm[17].x) / 3;
  const cy = (lm[0].y + lm[5].y + lm[17].y) / 3;
  posHist.push({ x: cx, y: cy }); while (posHist.length > 18) posHist.shift();
  const span = D(lm[0], lm[9]);
  sizeHist.push({ s: span, t: now }); while (sizeHist.length > 12) sizeHist.shift();

  const twirl = twirlAmount();
  const thrust = thrustSpeed();

  if (state !== "releasing" && pinchFrames < 6) {
    if (twirl > 2.4) { state = "charging"; charge = clamp(charge + dt * 0.7, 0, 1); }
    else if (state === "charging") { charge = clamp(charge - dt * 0.5, 0, 1); if (charge <= 0.01) state = "idle"; }

    if (state === "charging" && charge > 0.35 && thrust > 0.05) {
      release = { t0: now, x: px(lm[9]), y: py(lm[9]), r: baseR(lm) * (0.35 + charge * 0.95) };
      state = "releasing"; charge = 0;
    }
    if (state === "charging") label = charge > 0.7 ? "🌀 RASENGAN — thrust!" : "🌀 charging…";
  }
  setHint(label);
}

function twirlAmount() {
  if (posHist.length < 10) return 0;
  let mx = 0, my = 0; for (const p of posHist) { mx += p.x; my += p.y; } mx /= posHist.length; my /= posHist.length;
  let rad = 0; for (const p of posHist) rad += Math.hypot(p.x - mx, p.y - my); rad /= posHist.length;
  if (rad < 0.025) return 0;                         // not actually moving → no twirl
  let sum = 0;
  for (let i = 1; i < posHist.length; i++) {
    let d = Math.atan2(posHist[i].y - my, posHist[i].x - mx) - Math.atan2(posHist[i - 1].y - my, posHist[i - 1].x - mx);
    if (d > Math.PI) d -= 2 * Math.PI; if (d < -Math.PI) d += 2 * Math.PI; sum += d;
  }
  return Math.abs(sum);                              // radians of consistent rotation
}

function thrustSpeed() {
  if (sizeHist.length < 6) return 0;
  const a = sizeHist[sizeHist.length - 6], b = sizeHist[sizeHist.length - 1];
  return (b.s - a.s);                                // hand rapidly getting bigger = moving toward camera
}

const baseR = (lm) => Math.max(16, Math.hypot(px(lm[5]) - px(lm[17]), py(lm[5]) - py(lm[17])) * 1.3);

// ---- zoom bridge ----
function sendZoom(z, now) {
  const t = now || performance.now();
  if (z > 1 && t - zoomThrottle < 120) return;
  zoomThrottle = t;
  if (window.jarvis && window.jarvis.setZoom) window.jarvis.setZoom(z);
}
function setHint(t) { if (hint) { hint.textContent = t; hint.classList.toggle("show", !!t); } }

// ---------- rendering ----------
function render(now) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const lm = (lastResult && lastResult.landmarks && lastResult.landmarks[0]) || null;
  if (state === "charging" && lm && charge > 0.04) drawRasengan(px(lm[9]), py(lm[9]), baseR(lm) * (0.35 + charge * 0.95), now);
  if (state === "releasing") drawRelease(now);
}

function drawRasengan(cx, cy, r, t) {
  const rot = t * 0.007;
  ctx.save(); ctx.globalCompositeOperation = "lighter";
  let g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 1.7);
  g.addColorStop(0, "rgba(190,242,255,0.85)"); g.addColorStop(.3, "rgba(70,165,255,0.55)");
  g.addColorStop(.7, "rgba(30,90,225,0.22)"); g.addColorStop(1, "rgba(0,0,40,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, r * 1.7, 0, 7); ctx.fill();
  ctx.translate(cx, cy); ctx.rotate(rot); ctx.lineWidth = Math.max(1.5, r * 0.06);
  for (let i = 0; i < 3; i++) {
    ctx.rotate((Math.PI * 2) / 3); ctx.strokeStyle = "rgba(205,246,255,0.75)"; ctx.beginPath();
    for (let a = 0; a < Math.PI * 3.2; a += 0.18) { const rr = r * (a / (Math.PI * 3.2)); const x = Math.cos(a) * rr, y = Math.sin(a) * rr; a === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
    ctx.stroke();
  }
  ctx.rotate(-rot);
  let c = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 0.55);
  c.addColorStop(0, "rgba(255,255,255,0.95)"); c.addColorStop(.5, "rgba(150,215,255,0.7)"); c.addColorStop(1, "rgba(120,200,255,0)");
  ctx.fillStyle = c; ctx.beginPath(); ctx.arc(0, 0, r * 0.55, 0, 7); ctx.fill();
  for (let i = 0; i < 10; i++) { const a = rot * 3 + i * 0.63; const rr = r * (0.85 + 0.25 * Math.sin(t * 0.01 + i)); ctx.fillStyle = "rgba(225,248,255,0.85)"; ctx.beginPath(); ctx.arc(Math.cos(a) * rr, Math.sin(a) * rr, Math.max(1, r * 0.05), 0, 7); ctx.fill(); }
  ctx.restore();
}

function drawRelease(now) {
  const e = (now - release.t0) / 600;               // 0..1 over 600ms
  if (e >= 1) { state = "idle"; release = null; return; }
  const r = release.r * (1 + e * 2.4);
  const y = release.y - e * canvas.height * 0.5;     // shoots forward/away
  ctx.save(); ctx.globalCompositeOperation = "lighter"; ctx.globalAlpha = 1 - e;
  let g = ctx.createRadialGradient(release.x, y, 0, release.x, y, r);
  g.addColorStop(0, "rgba(255,255,255,0.9)"); g.addColorStop(.4, "rgba(90,180,255,0.6)"); g.addColorStop(1, "rgba(30,90,225,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(release.x, y, r, 0, 7); ctx.fill();
  ctx.restore();
}

init();
