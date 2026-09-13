// JARVIS console renderer.
//
// Four changes of substance over the previous version:
//
//   * The waveform was synthesized — a sine product that animated whenever the state was
//     "listening" or "speaking" and carried no information at all. The backend now publishes real
//     microphone RMS over SSE (`level` events, ~15/s from jarvis/audio/vad.py), so the meter shows
//     the actual room. The overlay still never opens the mic itself; only one process can hold
//     /dev/video0 and /dev/snd, and the Python voice loop owns them.
//
//   * The seven icon buttons had no keyboard path in a voice-first product. The text input is now
//     the universal entry point: it filters commands, contacts and past conversation, Enter runs
//     the top hit, and ⌘K opens an action panel in the browser top layer with light dismiss.
//
//   * Mode switching flipped `display`, so one element vanished and another appeared. It now runs
//     inside a View Transition, and the reactor physically travels into the panel header.
//
//   * State is pushed into the WebGL reactor rather than expressed as CSS animation-durations.

import { Reactor } from "./reactor.js";

// The port is normally injected by Electron; ?port= lets scripts/hud-shot.py aim the HUD at a
// running backend so screenshots show real health, telemetry and history.
const API = "http://127.0.0.1:" +
  (new URLSearchParams(location.search).get("port") || window.JARVIS_PORT || "8770");

const $ = (id) => document.getElementById(id);
const orb = $("orb");
const statusEl = $("status");
const logEl = $("log");
const input = $("msg");
const resultsEl = $("results");
const actionsEl = $("actions");
const apListEl = $("apList");

let mode = "voice";
let state = null;
let busy = false;

// ─────────────────────────────────────────────────────────────────────────
// reactor
// ─────────────────────────────────────────────────────────────────────────
const reactor = new Reactor($("reactorGl"));
if (reactor.unsupported) {
  document.body.classList.add("no-gl");
  console.warn("[hud] WebGL2 unavailable — using the CSS orb");
} else {
  reactor.syncTheme();
  reactor.start();
}

// ─────────────────────────────────────────────────────────────────────────
// mode + state
// ─────────────────────────────────────────────────────────────────────────
function applyMode(m) {
  mode = m;
  document.body.classList.toggle("text", m === "text");
  document.body.classList.toggle("voice", m === "voice");
  window.jarvis?.setMode?.(m);
}

function setMode(m) {
  if (m === mode) return;
  // startViewTransition does the Dynamic-Island morph for us: it snapshots both DOM states and
  // interpolates every element sharing a view-transition-name between them.
  const swap = () => {
    applyMode(m);
    if (m === "text") setTimeout(() => input.focus(), 60);
    else { input.blur(); hideResults(); }
    setState(null);
  };
  if (document.startViewTransition) document.startViewTransition(swap);
  else swap();
}

function setStatus(html) { statusEl.innerHTML = html; }

/** What the status line says when nothing is happening — depends on the mode, not the connection. */
function idleStatus() {
  return mode === "voice" ? '<span class="cue">say “Hey&nbsp;Jarvis”</span>' : "ready";
}

function setState(s) {
  state = s;
  document.body.classList.remove("listening", "thinking", "speaking", "error");
  if (s) document.body.classList.add(s);
  reactor.syncTheme();
  reactor.set({
    listen: s === "listening" ? 1 : 0,
    think: s === "thinking" ? 1 : 0,
    speak: s === "speaking" ? 1 : 0,
  });
  if (s === "listening") setStatus("listening");
  else if (s === "thinking") setStatus("working");
  else if (s === "speaking") setStatus("speaking");
  else if (s === "error") setStatus("offline");
  else setStatus(idleStatus());
  // When nothing is happening the meter should read zero rather than hold its last value.
  if (!s) reactor.set({ energy: 0 });
}

orb.addEventListener("click", () => setMode(mode === "voice" ? "text" : "voice"));
let wheelAt = 0;
orb.addEventListener("wheel", (e) => {
  e.preventDefault();
  const now = Date.now();
  if (now - wheelAt < 380) return;
  wheelAt = now;
  setMode(mode === "voice" ? "text" : "voice");
}, { passive: false });

// ─────────────────────────────────────────────────────────────────────────
// live audio meter — real levels, pushed from the backend
// ─────────────────────────────────────────────────────────────────────────
const meter = $("meter");
const mctx = meter.getContext("2d");
const BARS = 56;
let lastLevelAt = 0;
const history = new Float32Array(BARS);   // a scrolling window of recent amplitude
let energy = 0;

function pushLevel(v) {
  energy = v;
  lastLevelAt = performance.now();   // also stamps the harness's synthetic levels, so the decay
                                     // below only kicks in when the backend really has gone quiet
  history.copyWithin(0, 1);
  history[BARS - 1] = v;
  reactor.set({ energy: v });
}

function drawMeter() {
  requestAnimationFrame(drawMeter);
  const active = state === "listening" || state === "speaking";
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = meter.clientWidth * dpr, h = meter.clientHeight * dpr;
  if (meter.width !== w || meter.height !== h) { meter.width = w; meter.height = h; }
  mctx.clearRect(0, 0, w, h);
  if (!active) return;

  // Decay toward silence when no level event has arrived recently, so a dropped SSE frame reads
  // as the room going quiet rather than the meter freezing mid-word.
  if (performance.now() - lastLevelAt > 400) pushLevel(energy * 0.86);

  const css = getComputedStyle(document.body);
  const accent = css.getPropertyValue("--accent").trim() || "#37e7ff";
  const gap = w / BARS, mid = h / 2;
  mctx.fillStyle = accent;
  for (let i = 0; i < BARS; i++) {
    const v = history[i];
    // Taper the ends so the meter reads as a window onto a signal, not a boxed-in widget.
    const taper = Math.sin((i / (BARS - 1)) * Math.PI) ** 0.5;
    const bar = Math.max(1.5 * dpr, v * taper * (h - 4 * dpr));
    mctx.globalAlpha = 0.28 + 0.72 * v;
    mctx.fillRect(i * gap + gap * 0.22, mid - bar / 2, gap * 0.56, bar);
  }
  mctx.globalAlpha = 1;
}
drawMeter();

// ─────────────────────────────────────────────────────────────────────────
// conversation log
// ─────────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function fmt(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\n/g, "<br>");
}
function addMsg(who, text, animate = true) {
  const d = document.createElement("div");
  d.className = "msgline " + who;
  d.innerHTML = who === "sys" ? fmt(text)
    : `<span class="who">${who === "you" ? "YOU" : "JARVIS"}</span>${fmt(text)}`;
  if (!animate) d.style.animation = "none";
  logEl.appendChild(d);
  logEl.scrollTop = logEl.scrollHeight;
  while (logEl.children.length > 90) logEl.removeChild(logEl.firstChild);
  return d;
}

async function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  busy = true;
  if (mode !== "text") setMode("text");
  hideResults();
  addMsg("you", text);
  setState("thinking");

  // Stream the reply so words appear as the model writes them. A local 3B model takes a few
  // seconds for two sentences, and watching nothing happen for all of it makes the assistant
  // feel far slower than it is. Falls back to the plain endpoint if streaming is unavailable.
  let bubble = null;
  let acc = "";
  try {
    const r = await fetch(API + "/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    if (!r.ok || !r.body) throw new Error("no stream");

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line; keep any partial frame in the buffer.
      const frames = buf.split("\n\n");
      buf = frames.pop() ?? "";
      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let d; try { d = JSON.parse(line.slice(5).trim()); } catch { continue; }
        if (d.kind === "delta") {
          if (!bubble) { bubble = addMsg("jarvis", ""); bubble.classList.add("streaming"); setState("speaking"); }
          acc += d.text;
          bubble.innerHTML = `<span class="who">JARVIS</span>${fmt(acc)}`;
          logEl.scrollTop = logEl.scrollHeight;
        } else if (d.kind === "final") {
          acc = d.text || acc;
          if (!bubble) bubble = addMsg("jarvis", "");
          bubble.innerHTML = `<span class="who">JARVIS</span>${fmt(acc || "(no reply)")}`;
          bubble.classList.remove("streaming");
        }
      }
    }
    if (!bubble) addMsg("jarvis", acc || "(no reply)");
    setState(null);
  } catch {
    if (bubble) bubble.classList.remove("streaming");
    if (!acc) {
      addMsg("sys", "backend offline — it should restart itself; see /tmp/jarvis-web.log");
      setState("error");
      setTimeout(() => setState(null), 3000);
    } else {
      setState(null);
    }
  }
  busy = false;
}

// ─────────────────────────────────────────────────────────────────────────
// command palette
//
// The Raycast contract: one filter input, a result list, Enter runs the selected result. Commands
// are prompts to the brain; contacts and history are shortcuts into them. Everything here is
// local — no extra endpoint, no network round trip while typing.
// ─────────────────────────────────────────────────────────────────────────
const COMMANDS = [
  { icon: "◧", title: "Catch me up",        kind: "brief",  say: "What did I miss? Summarise messages, mail and anything important." },
  { icon: "▤", title: "Today's agenda",     kind: "cal",    say: "What's on my calendar today and what's my next meeting?" },
  { icon: "✉", title: "Check mail",         kind: "mail",   say: "Check my email and summarise anything that needs a reply." },
  { icon: "◉", title: "Read my screen",     kind: "vision", say: "Take a screenshot and tell me what's on my screen." },
  { icon: "▮", title: "System status",      kind: "sys",    say: "Give me a full system status report." },
  { icon: "☾", title: "Focus mode",         kind: "focus",  say: "I'm heads-down — hold non-urgent notifications and cover my messages." },
  { icon: "☀", title: "I'm back",           kind: "focus",  say: "I'm back, resume normal notifications." },
  { icon: "◐", title: "Who's nearby",       kind: "radar",  say: "Who is around me right now?" },
  { icon: "✆", title: "WhatsApp summary",   kind: "msg",    say: "Check my WhatsApp and summarise anything that needs a reply." },
  { icon: "⌕", title: "Deep research…",     kind: "research", prefix: "Research this thoroughly: " },
  { icon: "▣", title: "Mirror my phone",    kind: "phone",  action: () => window.jarvis?.launchPhone?.() },
];

let results = [];
let selected = 0;

function score(hay, needle) {
  // Subsequence match with a bonus for a prefix hit — enough for a list this size, and it means
  // "sysstat" still finds "System status".
  hay = hay.toLowerCase(); needle = needle.toLowerCase();
  if (!needle) return 0.1;
  if (hay.startsWith(needle)) return 10;
  if (hay.includes(needle)) return 5;
  let i = 0;
  for (const ch of hay) if (ch === needle[i]) i++;
  return i === needle.length ? 1 : 0;
}

function buildResults(q) {
  const out = [];
  for (const c of COMMANDS) {
    const s = score(c.title, q);
    if (s > 0) out.push({ ...c, sub: c.say ? c.say.slice(0, 70) : "", _s: s });
  }
  for (const h of recentTurns) {
    const s = q ? score(h.text, q) : 0;
    if (s > 0) out.push({ icon: "↺", title: h.text.slice(0, 62), kind: "recent", say: h.text, sub: "re-ask", _s: s * 0.6 });
  }
  return out.sort((a, b) => b._s - a._s).slice(0, 7);
}

let recentTurns = [];

function renderResults() {
  if (!results.length) { hideResults(); return; }
  resultsEl.hidden = false;
  resultsEl.innerHTML = results.map((r, i) => `
    <div class="result" role="option" aria-selected="${i === selected}" data-i="${i}">
      <span class="rIcon">${r.icon}</span>
      <span class="rBody">
        <span class="rTitle">${esc(r.title)}</span>
        ${r.sub ? `<span class="rSub">${esc(r.sub)}</span>` : ""}
      </span>
      <span class="rKind">${esc(r.kind)}</span>
    </div>`).join("");
  for (const el of resultsEl.querySelectorAll(".result")) {
    el.onclick = () => runResult(results[+el.dataset.i]);
  }
}

function hideResults() { resultsEl.hidden = true; resultsEl.innerHTML = ""; results = []; selected = 0; }

function runResult(r) {
  if (!r) return;
  if (r.action) { r.action(); input.value = ""; hideResults(); return; }
  if (r.prefix) { input.value = r.prefix; hideResults(); input.focus(); return; }
  input.value = "";
  send(r.say);
}

input.addEventListener("input", () => {
  const q = input.value.trim();
  // A question is a question — only offer the palette for short, command-shaped input.
  results = q.length && q.length < 28 && !q.includes("?") ? buildResults(q) : [];
  selected = 0;
  renderResults();
});

input.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" && results.length) {
    e.preventDefault(); selected = (selected + 1) % results.length; renderResults();
  } else if (e.key === "ArrowUp" && results.length) {
    e.preventDefault(); selected = (selected - 1 + results.length) % results.length; renderResults();
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (results.length) runResult(results[selected]);
    else { const v = input.value; input.value = ""; send(v); }
  } else if (e.key === "Escape") {
    if (results.length) hideResults();
    else setMode("voice");
  }
});

// ─────────────────────────────────────────────────────────────────────────
// action panel (⌘K / Ctrl+K)
// ─────────────────────────────────────────────────────────────────────────
const ACTIONS = [
  { title: "Switch to voice", key: "esc", run: () => setMode("voice") },
  { title: "Clear conversation", key: "⌘⌫", run: clearHistory },
  { title: "Mirror my phone", key: "", run: () => window.jarvis?.launchPhone?.() },
  { title: "System status", key: "", run: () => send("Give me a full system status report.") },
  { title: "Hide the HUD", key: "^⌘H", run: () => window.jarvis?.hide?.() },
];
let apSelected = 0;

function renderActions() {
  apListEl.innerHTML = ACTIONS.map((a, i) => `
    <button class="apItem" role="option" aria-selected="${i === apSelected}" data-i="${i}">
      ${esc(a.title)}${a.key ? `<span class="k">${esc(a.key)}</span>` : ""}
    </button>`).join("");
  for (const el of apListEl.querySelectorAll(".apItem")) {
    el.onclick = () => { actionsEl.hidePopover(); ACTIONS[+el.dataset.i].run(); };
  }
}

function toggleActions() {
  if (!actionsEl.togglePopover) return;          // very old runtime: no top layer, skip silently
  apSelected = 0; renderActions();
  actionsEl.togglePopover();
}

window.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); toggleActions(); return; }
  if (actionsEl.matches?.(":popover-open")) {
    if (e.key === "ArrowDown") { e.preventDefault(); apSelected = (apSelected + 1) % ACTIONS.length; renderActions(); }
    if (e.key === "ArrowUp") { e.preventDefault(); apSelected = (apSelected - 1 + ACTIONS.length) % ACTIONS.length; renderActions(); }
    if (e.key === "Enter") { e.preventDefault(); actionsEl.hidePopover(); ACTIONS[apSelected].run(); }
    return;
  }
  // Typing anywhere drops you into the input — the assistant should never make you aim at a box.
  if (mode === "voice" && e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
    setMode("text");
    setTimeout(() => { input.value = e.key; input.dispatchEvent(new Event("input")); }, 80);
  }
});

$("clearBtn").onclick = clearHistory;
async function clearHistory() {
  try { await fetch(API + "/history", { method: "DELETE" }); } catch {}
  logEl.innerHTML = "";
  recentTurns = [];
  addMsg("sys", "conversation cleared");
}

// ─────────────────────────────────────────────────────────────────────────
// live events
// ─────────────────────────────────────────────────────────────────────────
let linkTimer = null;

function connectEvents() {
  let es;
  try { es = new EventSource(API + "/events"); } catch { return; }
  es.onopen = () => { clearTimeout(linkTimer); linkTimer = null; if (!state) setStatus(idleStatus()); };
  es.onmessage = (ev) => {
    clearTimeout(linkTimer); linkTimer = null;
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    onEvent(d.kind, d.text || "");
  };
  es.onerror = () => {
    // EventSource reconnects on its own, and it fires onerror on every transient blip. Reporting
    // that instantly used to overwrite the live state — a hiccup mid-utterance replaced
    // "listening" with "linking…" and the HUD looked broken while Jarvis was working fine. Only
    // say anything if we are idle AND the gap outlasts a reconnect.
    if (state || linkTimer) return;
    linkTimer = setTimeout(() => { if (!state) setStatus("linking…"); }, 4000);
  };
}
connectEvents();

let speakingTimer = null;

function onEvent(kind, text) {
  switch (kind) {
    case "ready":  setState(null); break;
    case "wake":   setState("listening"); break;
    case "level":  pushLevel(parseFloat(text) || 0); break;
    case "heard":  addMsg("you", text); recentTurns.unshift({ text }); recentTurns = recentTurns.slice(0, 12); setState("thinking"); break;
    case "reply":
      addMsg("jarvis", text);
      setState("speaking");
      clearTimeout(speakingTimer);
      // Rough speech duration: ~14 characters a second, floored and capped.
      speakingTimer = setTimeout(() => setState(null), Math.min(14000, 1400 + text.length * 70));
      break;
    case "barge_in": setState(null); toast("interrupted"); break;
    case "phone":  addMsg("sys", "▣ " + text); toast(text); break;
    case "presence": reactor.set({ presence: Math.min(1, (parseInt(text, 10) || 0) / 2) }); break;
    case "sleep":  setState(null); break;
    case "power":  toast(text === "asleep" ? "going quiet" : "awake"); break;
    case "coding_job": break;                       // handled by the ambient window
    case "sports_toggle": window.jarvis?.sportsToggle?.(text); break;
    default: break;
  }
}

// ─────────────────────────────────────────────────────────────────────────
// health + telemetry
// ─────────────────────────────────────────────────────────────────────────
const sysdotsEl = $("sysdots");
const brainEl = $("brainlabel");
const telEl = $("telemetry");

async function pollHealth() {
  try {
    const h = await (await fetch(API + "/health")).json();
    if (h.error) return;
    sysdotsEl.innerHTML = (h.systems || [])
      .map((s) => `<span class="d ${s.ok ? "on" : ""}" title="${esc(s.name)}: ${esc(s.label)}"></span>`).join("");
    brainEl.textContent = `${h.brain || "?"} · ${h.online ?? "?"}/${h.total ?? "?"}`;
  } catch {}
}

function tel(label, value, pct) {
  const cls = pct >= 90 ? "crit" : pct >= 75 ? "hot" : "";
  return `<span class="t ${cls}">${label}<b>${value}</b></span>`;
}

async function pollStats() {
  try {
    const s = await (await fetch(API + "/stats")).json();
    if (s.error) return;
    const bits = [];
    const cpu = Math.round(s.cpu_percent ?? 0);
    bits.push(tel("CPU", cpu + "%", cpu));
    let gpuPct = 0, battPct = 0;
    if (s.mem) bits.push(tel("MEM", Math.round(s.mem.percent) + "%", s.mem.percent));
    if (s.gpu) { gpuPct = s.gpu.util ?? 0; bits.push(tel("GPU", gpuPct + "%", gpuPct)); }
    if (s.battery) {
      battPct = s.battery.percent ?? 0;
      bits.push(tel("BAT", battPct + "%" + (s.battery.plugged ? "+" : ""), 100 - battPct));
    }
    telEl.innerHTML = bits.join("");
    // The reactor's arcs are these same numbers — the gauge is the reading, not an ornament.
    reactor.set({
      cpu: cpu / 100,
      mem: (s.mem?.percent ?? 0) / 100,
      gpu: gpuPct / 100,
      batt: battPct / 100,
    });
  } catch {
    telEl.textContent = "offline";
  }
}

pollHealth(); setInterval(pollHealth, 8000);
pollStats(); setInterval(pollStats, 2500);

// ─────────────────────────────────────────────────────────────────────────
// webcam — only if the overlay owns it (the backend presence service usually does)
// ─────────────────────────────────────────────────────────────────────────
const OVERLAY_OWNS_CAMERA = window.jarvis?.overlayCamera === true;

async function initCamera(retries = 3) {
  if (!OVERLAY_OWNS_CAMERA) {
    console.log("[camera] backend presence service owns the webcam (JARVIS_OVERLAY_CAMERA=1 to override)");
    return;
  }
  for (let i = 0; i < retries; i++) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" } });
      $("cam").srcObject = stream;
      return;
    } catch (e) {
      console.warn(`[camera] attempt ${i + 1}/${retries}:`, e.name);
      if (i < retries - 1) await new Promise((r) => setTimeout(r, 2000));
    }
  }
}
initCamera();

// ─────────────────────────────────────────────────────────────────────────
// clock + toast + boot
// ─────────────────────────────────────────────────────────────────────────
function tick() {
  $("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
tick(); setInterval(tick, 1000);

const toastEl = $("toast");
let toastTimer;
function toast(m) {
  toastEl.textContent = m;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 3200);
}
window.jarvis?.onToast?.(toast);

// Driven by scripts/hud-shot.py so the screenshot harness exercises the real state machine
// rather than poking CSS classes — otherwise the meter and status line are never exercised and
// a broken one photographs as "fine".
window.__hud = { setState, setMode, applyMode, pushLevel, addMsg, reactor, onEvent };

(async () => {
  try {
    const msgs = (await (await fetch(API + "/history?limit=10")).json()).messages || [];
    for (const m of msgs) addMsg(m.who, m.text, false);
    recentTurns = msgs.filter((m) => m.who === "you").map((m) => ({ text: m.text })).reverse().slice(0, 12);
    if (msgs.length) addMsg("sys", "session restored");
  } catch {}
  setState(null);
})();
