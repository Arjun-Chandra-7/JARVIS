// JARVIS console renderer — mode toggle (scroll/click the orb), chat, live events, buttons.
const API = "http://127.0.0.1:" + (window.JARVIS_PORT || "8770");

const orb = document.getElementById("orb");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const input = document.getElementById("msg");
let mode = "voice";

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
function setStatus(t) { statusEl.textContent = t; }
function setState(s) {
  document.body.classList.remove("listening", "thinking", "speaking");
  if (s) document.body.classList.add(s);
  setStatus(s === "listening" ? "listening…" : s === "thinking" ? "thinking…" : s === "speaking" ? "speaking…"
    : (mode === "voice" ? "say “Hey Jarvis”" : "ready"));
}

// ---------- chat log ----------
function addMsg(who, text) {
  const d = document.createElement("div");
  d.className = "msgline " + who;
  if (who === "sys") d.textContent = text;
  else d.innerHTML = `<span class="who">${who === "you" ? "YOU" : "JARVIS"}</span>${escapeHtml(text)}`;
  logEl.appendChild(d);
  logEl.scrollTop = logEl.scrollHeight;
  while (logEl.children.length > 60) logEl.removeChild(logEl.firstChild);
}
function escapeHtml(s) { const p = document.createElement("p"); p.textContent = s; return p.innerHTML; }

async function send(text) {
  if (!text.trim()) return;
  if (mode !== "text") setMode("text");
  addMsg("you", text);
  setState("thinking");
  try {
    const r = await fetch(API + "/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
    const j = await r.json();
    addMsg("jarvis", j.reply || "(no reply)");
  } catch (e) {
    addMsg("sys", "⚠ backend offline — start it with:  python -m jarvis --web");
  }
  setState(null);
}
input.addEventListener("keydown", (e) => { if (e.key === "Enter") { send(input.value); input.value = ""; } });

// ---------- feature buttons ----------
document.querySelectorAll(".act").forEach((b) => {
  b.onclick = () => {
    if (b.dataset.cmd === "__phone") { window.jarvis.launchPhone(); return; }
    if (b.id === "awayBtn") {
      b.classList.toggle("on");
      send(b.classList.contains("on") ? "I'm not available, cover my messages and calls." : "I'm back, turn off away mode.");
      return;
    }
    if (b.dataset.say) send(b.dataset.say);
  };
});

// ---------- live voice/phone events ----------
try {
  const es = new EventSource(API + "/events");
  es.onmessage = (ev) => { let d; try { d = JSON.parse(ev.data); } catch { return; } onEvent(d.kind, d.text || ""); };
  es.onerror = () => setStatus("offline");
} catch (e) {}
function onEvent(kind, text) {
  if (kind === "ready") setState(null);
  else if (kind === "wake") setState("listening");
  else if (kind === "heard") { addMsg("you", text); setState("thinking"); }
  else if (kind === "reply") { addMsg("jarvis", text); setState("speaking"); setTimeout(() => setState(null), Math.min(6000, 1600 + text.length * 28)); }
  else if (kind === "phone") addMsg("sys", "📱 " + text);
  else if (kind === "sleep") setState(null);
}

// ---------- webcam (also feeds hand-gestures) ----------
navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } })
  .then((s) => { document.getElementById("cam").srcObject = s; }).catch(() => {});

// ---------- clock ----------
function tick() { document.getElementById("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
tick(); setInterval(tick, 1000);

// ---------- toast ----------
const toastEl = document.getElementById("toast");
window.jarvis.onToast((m) => { toastEl.textContent = m; toastEl.classList.add("show"); setTimeout(() => toastEl.classList.remove("show"), 3000); });

setState(null);
addMsg("sys", "JARVIS online · scroll or click the orb for voice / text");
