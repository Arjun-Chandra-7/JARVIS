// JARVIS console renderer — mode toggle (scroll/click the orb), chat, live events, buttons,
// always-listening cue, animated waveform, live subsystem health + telemetry.
const API = "http://127.0.0.1:" + (window.JARVIS_PORT || "8770");

const orb = document.getElementById("orb");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const input = document.getElementById("msg");
let mode = "voice";
let curState = null;

// ---------- mode: voice <-> text (scroll or click the mic) ----------
function setMode(m) {
  mode = m;
  document.body.classList.toggle("text", m === "text");
  document.body.classList.toggle("voice", m === "voice");
  window.jarvis.setMode(m);
  if (m === "text") setTimeout(() => input.focus(), 340);
  setState(null);
}
orb.addEventListener("click", () => setMode(mode === "voice" ? "text" : "voice"));
let wheelLock = 0;
orb.addEventListener("wheel", (e) => {
  e.preventDefault();
  const now = Date.now();
  if (now - wheelLock < 400) return;
  wheelLock = now;
  setMode(mode === "voice" ? "text" : "voice");
}, { passive: false });

// ---------- state / status ----------
function setStatus(html) { statusEl.innerHTML = html; }
function setState(s) {
  curState = s;
  document.body.classList.remove("listening", "thinking", "speaking");
  if (s) document.body.classList.add(s);
  if (s === "listening") setStatus("listening…");
  else if (s === "thinking") setStatus("thinking…");
  else if (s === "speaking") setStatus("speaking…");
  else if (mode === "voice") setStatus('<span class="cue">◉ say “Hey&nbsp;Jarvis”</span>');
  else setStatus("ready");
}

// ---------- animated waveform (synthesized — no mic contention with the voice loop) ----------
const wave = document.getElementById("wave");
const wctx = wave.getContext("2d");
let wt = 0;
function drawWave() {
  requestAnimationFrame(drawWave);
  const active = curState === "listening" || curState === "speaking";
  wctx.clearRect(0, 0, wave.width, wave.height);
  if (!active) return;
  wt += curState === "speaking" ? 0.34 : 0.22;
  const bars = 28, gap = wave.width / bars, mid = wave.height / 2;
  const col = getComputedStyle(document.body).getPropertyValue("--accent").trim() || "#37e7ff";
  wctx.fillStyle = col;
  for (let i = 0; i < bars; i++) {
    const env = Math.sin((i / bars) * Math.PI);               // taper at the ends
    const amp = env * (0.35 + 0.65 * Math.abs(Math.sin(wt + i * 0.5) * Math.sin(wt * 0.7 + i)));
    const h = Math.max(2, amp * (wave.height - 4));
    wctx.globalAlpha = 0.55 + 0.45 * amp;
    wctx.fillRect(i * gap + 1, mid - h / 2, gap - 2, h);
  }
  wctx.globalAlpha = 1;
}
drawWave();

// ---------- chat log ----------
function fmt(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\n/g, "<br>");
}
function addMsg(who, text, animate = true) {
  const d = document.createElement("div");
  d.className = "msgline " + who;
  if (who === "sys") d.innerHTML = fmt(text);
  else d.innerHTML = `<span class="who">${who === "you" ? "YOU" : "JARVIS"}</span>${fmt(text)}`;
  if (!animate) d.style.animation = "none";
  logEl.appendChild(d);
  logEl.scrollTop = logEl.scrollHeight;
  while (logEl.children.length > 80) logEl.removeChild(logEl.firstChild);
}

let busy = false;
async function send(text) {
  if (!text.trim() || busy) return;
  busy = true;
  if (mode !== "text") setMode("text");
  addMsg("you", text);
  setState("thinking");
  try {
    const r = await fetch(API + "/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
    const j = await r.json();
    addMsg("jarvis", j.reply || "(no reply)");
  } catch (e) {
    addMsg("sys", "⚠ backend offline — it should auto-start; check /tmp/jarvis-web.log");
  }
  setState(null);
  busy = false;
}
input.addEventListener("keydown", (e) => { if (e.key === "Enter") { send(input.value); input.value = ""; } });

// ---------- feature buttons ----------
document.querySelectorAll(".act").forEach((b) => {
  b.onclick = () => {
    if (b.dataset.cmd === "__phone") { window.jarvis.launchPhone(); return; }
    if (b.id === "awayBtn") {
      b.classList.toggle("on");
      send(b.classList.contains("on")
        ? "I'm heads-down — hold non-urgent notifications and cover my messages."
        : "I'm back, resume normal notifications.");
      return;
    }
    if (b.dataset.say) send(b.dataset.say);
  };
});

// ---------- live voice/phone events ----------
try {
  const es = new EventSource(API + "/events");
  es.onmessage = (ev) => { let d; try { d = JSON.parse(ev.data); } catch { return; } onEvent(d.kind, d.text || ""); };
  es.onerror = () => { if (!curState) setStatus("linking…"); };
} catch (e) {}
function onEvent(kind, text) {
  if (kind === "ready") setState(null);
  else if (kind === "wake") setState("listening");
  else if (kind === "heard") { addMsg("you", text); setState("thinking"); }
  else if (kind === "reply") { addMsg("jarvis", text); setState("speaking"); setTimeout(() => setState(null), Math.min(6000, 1600 + text.length * 28)); }
  else if (kind === "phone") { addMsg("sys", "📱 " + text); toast("📱 " + text); }
  else if (kind === "sleep") setState(null);
  else if (kind === "sports_toggle") { window.jarvis.sportsToggle(text); }
}

// ---------- live subsystem health → header dots + mini line ----------
const miniEl = document.getElementById("mini");
const sysdotsEl = document.getElementById("sysdots");
async function pollHealth() {
  try {
    const h = await (await fetch(API + "/health")).json();
    if (h.error) return;
    sysdotsEl.innerHTML = (h.systems || [])
      .map((s) => `<span class="d ${s.ok ? "on" : ""}" title="${s.name}: ${s.label}"></span>`).join("");
    document.getElementById("ehint").textContent = `${h.brain} · ${h.online}/${h.total} systems`;
  } catch {}
}
async function pollStats() {
  try {
    const s = await (await fetch(API + "/stats")).json();
    if (s.error) return;
    let bits = [`CPU ${Math.round(s.cpu_percent)}%`];
    if (s.mem) bits.push(`MEM ${Math.round(s.mem.percent)}%`);
    if (s.gpu) bits.push(`GPU ${s.gpu.util}%`);
    if (s.battery) bits.push(`🔋${s.battery.percent}%${s.battery.plugged ? "⚡" : ""}`);
    miniEl.textContent = bits.join("  ·  ");
  } catch { miniEl.textContent = "offline"; }
}
pollHealth(); setInterval(pollHealth, 8000);
pollStats(); setInterval(pollStats, 3000);

// ---------- webcam ----------
// Only one process can hold /dev/video0. The backend presence service (jarvis/presence)
// is the owner by default: it runs headless, so the radar keeps working whether or not
// this overlay window is open. Set JARVIS_OVERLAY_CAMERA=1 to hand the camera back to
// the overlay instead (enables in-browser hand gestures, disables the backend radar).
const OVERLAY_OWNS_CAMERA = window.jarvis?.overlayCamera === true;

async function initCamera(retries = 3) {
  if (!OVERLAY_OWNS_CAMERA) {
    console.log("[camera] backend presence service owns the webcam (JARVIS_OVERLAY_CAMERA=1 to override)");
    return;
  }
  for (let i = 0; i < retries; i++) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" } });
      document.getElementById("cam").srcObject = stream;
      console.log("[camera] ✓ webcam stream active");
      return;
    } catch (e) {
      console.warn(`[camera] attempt ${i + 1}/${retries} failed:`, e.name, e.message);
      if (i < retries - 1) await new Promise(r => setTimeout(r, 2000));
    }
  }
  console.error("[camera] could not access webcam — face detection disabled");
}
initCamera();

// ---------- clock ----------
function tick() { document.getElementById("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
tick(); setInterval(tick, 1000);

// ---------- toast ----------
const toastEl = document.getElementById("toast");
let toastT;
function toast(m) { toastEl.textContent = m; toastEl.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove("show"), 3000); }
window.jarvis.onToast(toast);

// ---------- boot: restore recent conversation ----------
(async () => {
  try {
    const msgs = (await (await fetch(API + "/history?limit=8")).json()).messages || [];
    for (const m of msgs) addMsg(m.who, m.text, false);
    if (msgs.length) addMsg("sys", "— session restored —");
  } catch {}
  setState(null);
  addMsg("sys", "JARVIS online · always listening · scroll the orb for text");
})();

