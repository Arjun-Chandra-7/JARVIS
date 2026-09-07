// Ambient HUD layer — read-only, NEVER interactive. Everything shown here is LIVE:
// system telemetry, subsystem health (green/red), the WhatsApp feed, weather, and a
// real proximity radar (nearby WiFi / LAN / Bluetooth devices).
const API = "http://127.0.0.1:" + (window.JARVIS_PORT || "8770");
const $ = (id) => document.getElementById(id);

// ---------- clock + date ----------
function tick() {
  const n = new Date();
  $("aclock").textContent = n.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("adate").textContent = n.toLocaleDateString([], { weekday: "short", day: "2-digit", month: "short" });
}
tick(); setInterval(tick, 1000);

// ---------- telemetry (CPU / MEM / GPU / NET throughput) ----------
let lastNet = null;
function bar(valId, barId, pct, label, hot) {
  $(valId).textContent = label;
  const b = $(barId);
  b.style.width = Math.max(3, Math.min(100, pct)) + "%";
  b.classList.toggle("hot", !!hot);
}
async function stats() {
  try {
    const d = await (await fetch(API + "/stats")).json();
    if (d && d.cpu_percent != null) {
      bar("cpu", "cpubar", Math.round(d.cpu_percent), Math.round(d.cpu_percent) + "%" + (d.cpu_temp ? " " + Math.round(d.cpu_temp) + "°" : ""), d.cpu_percent > 85);
      bar("mem", "membar", Math.round(d.mem.percent), Math.round(d.mem.percent) + "%", d.mem.percent > 88);
      if (d.gpu) bar("gpu", "gpubar", d.gpu.util, d.gpu.util + "% " + d.gpu.temp + "°", d.gpu.util > 90);
      else bar("gpu", "gpubar", 0, "n/a");
    }
  } catch (e) {}
}
stats(); setInterval(stats, 3000);

// NET throughput uses /health uptime? no — derive from /stats net if present; else animate off.
// (kept simple: show a live pseudo from GPU mem pressure is misleading, so we read real net via
//  the browser is impossible; instead we surface disk % as a steady real bar fallback.)
async function net() {
  try {
    const d = await (await fetch(API + "/stats")).json();
    if (d && d.disk) bar("net", "netbar", d.disk.percent, "DISK " + Math.round(d.disk.percent) + "%", d.disk.percent > 92);
  } catch (e) {}
}
net(); setInterval(net, 5000);

// ---------- subsystem health (green/red) ----------
async function health() {
  try {
    const h = await (await fetch(API + "/health")).json();
    if (h.error) return;
    $("sysratio").textContent = h.online + "/" + h.total;
    $("syslist").innerHTML = (h.systems || []).map((s) =>
      `<div class="srow ${s.ok ? "on" : ""}"><span class="d"></span><span class="nm">${s.name}</span><span class="meta">${s.label}</span></div>`
    ).join("");
  } catch (e) {}
}
health(); setInterval(health, 8000);

// ---------- weather ----------
async function weather() {
  try {
    const w = (await (await fetch(API + "/weather")).json()).weather;
    if (w) $("awx").textContent = `${w.temp_c}° ${w.desc} · ${w.city}`;
  } catch (e) {}
}
weather(); setInterval(weather, 900000);

// ---------- WhatsApp live feed ----------
function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, (Date.now() - ts * 1000) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
}
async function whatsapp() {
  try {
    const r = await (await fetch(API + "/whatsapp/inbox")).json();
    const msgs = (r.messages || []).slice(-6).reverse();
    $("wacount").textContent = msgs.length ? msgs.length : "·";
    if (!msgs.length) { $("wafeed").innerHTML = '<div class="empty">no recent messages</div>'; return; }
    $("wafeed").innerHTML = msgs.map((m) => {
      const who = (m.name || m.from || "unknown").toString();
      const txt = (m.text || "").toString();
      return `<div class="wamsg"><div class="from"><span>${esc(who)}</span><time>${ago(m.ts)}</time></div><div class="txt">${esc(txt)}</div></div>`;
    }).join("");
  } catch (e) { $("wafeed").innerHTML = '<div class="empty">bridge offline</div>'; }
}
function esc(s) { const p = document.createElement("p"); p.textContent = s; return p.innerHTML; }
whatsapp(); setInterval(whatsapp, 6000);

// ---------- PROXIMITY radar (real nearby devices) ----------
function hashAngle(str) { let h = 0; for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) & 0xffff; return (h % 360) * Math.PI / 180; }


// ---------- state reactions from the live event stream ----------
function state(s) {
  document.body.classList.remove("listening", "thinking", "speaking", "idle");
  document.body.classList.add(s || "idle");
  $("astatus").textContent = s === "listening" ? "listening" : s === "thinking" ? "processing" : s === "speaking" ? "responding" : "awaiting · hey jarvis";
}
try {
  const es = new EventSource(API + "/events");
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (d.kind === "wake") state("listening");
    else if (d.kind === "heard") state("thinking");
    else if (d.kind === "reply") { state("speaking"); setTimeout(() => state(null), Math.min(6000, 1600 + (d.text || "").length * 28)); }
    else if (d.kind === "sleep" || d.kind === "ready") state(null);
    else if (d.kind === "phone") whatsapp();   // refresh feed on new phone/WA event
    else if (d.kind === "presence_data") updateHumanRadar(d.text);
    else if (d.kind === "presence_status") updateRadarStatus(d.text);
  };
} catch (e) {}

// ---------- ACOUSTIC ACTIVE SONAR & HUMAN PROXIMITY RADAR ----------
// Transmits inaudible ultrasound chirps (18.5kHz-20kHz) from speakers and captures
// acoustic reflections / Doppler shifts off human bodies via microphone in real-time.

function stringToHue(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) hash = (hash * 31 + str.charCodeAt(i)) & 0xffff;
  return hash;
}

async function refreshRadar() {
  const host = $("hblips");
  const countEl = $("nearcount");
  if (!host) return;
  let data = null;
  try { data = await (await fetch(API + "/radar")).json(); } catch (e) { data = null; }

  if (!data || data.error) { countEl.textContent = "RADAR OFFLINE"; host.innerHTML = ""; return; }

  const contacts = data.contacts || [];
  const located = contacts.filter(c => c.bearing_known && c.y_m != null);
  const ranged  = contacts.filter(c => !c.bearing_known && c.distance_m != null);
  const named   = contacts.filter(c => !c.bearing_known && c.distance_m == null && c.label);

  // Headline reflects what we can actually stand behind, not raw contact count.
  const people = data.people || 0;
  if (people > 0)          countEl.textContent = people === 1 ? "1 PERSON" : `${people} PEOPLE`;
  else if (ranged.length)  countEl.textContent = `${ranged.length} MOVING`;
  else {
    const blocked = (data.sensors || []).find(s => !s.ok && s.detail);
    countEl.textContent = blocked ? "NO SIGNAL" : "CLEAR";
  }

  const MAX_M = 5.0, HALF_FOV = 34;   // matches the camera's horizontal half-FOV
  if (!window._blipMap) window._blipMap = new Map();
  const blipMap = window._blipMap;
  const alive = new Set();

  // --- located people: a real top-down polar position -----------------------
  for (const c of located) {
    alive.add(c.id);
    let el = blipMap.get(c.id);
    if (!el) {
      el = document.createElement("div");
      el.className = "hblip";
      el.innerHTML = '<span class="htag"></span><span class="hdist"></span>';
      host.appendChild(el); blipMap.set(c.id, el);
    }
    el.classList.remove("harc");
    const xPct = 50 + Math.max(-1, Math.min(1, (c.bearing_deg || 0) / HALF_FOV)) * 40;
    const yPct = 96 - Math.max(0, Math.min(1, (c.y_m || 0) / MAX_M)) * 88;  // near = bottom
    el.style.left = xPct.toFixed(1) + "%";
    el.style.top = yPct.toFixed(1) + "%";
    const size = Math.max(9, 19 - (c.distance_m / MAX_M) * 10);
    el.style.width = el.style.height = size.toFixed(0) + "px";
    el.style.opacity = String(0.45 + 0.55 * (c.confidence || 0.5));
    el.querySelector(".htag").textContent = c.label || "PERSON";
    el.querySelector(".hdist").textContent =
      `${c.distance_m.toFixed(1)}m ${c.bearing_deg > 0 ? "+" : ""}${Math.round(c.bearing_deg)}\u00b0`;
  }

  // --- range-only contacts: an ARC, because the bearing is genuinely unknown --
  for (const c of ranged) {
    alive.add(c.id);
    let el = blipMap.get(c.id);
    if (!el) {
      el = document.createElement("div");
      el.innerHTML = '<span class="htag"></span>';
      host.appendChild(el); blipMap.set(c.id, el);
    }
    el.className = "hblip harc";
    el.style.width = el.style.height = "";
    el.style.left = "50%";
    el.style.top = (96 - Math.max(0, Math.min(1, c.distance_m / MAX_M)) * 88).toFixed(1) + "%";
    el.style.opacity = String(0.35 + 0.5 * (c.confidence || 0.5));
    el.querySelector(".htag").textContent = `${c.distance_m.toFixed(1)}m \u00b7 bearing unknown`;
  }

  for (const [id, el] of blipMap.entries()) {
    if (!alive.has(id)) { el.remove(); blipMap.delete(id); }
  }

  // --- legend tells the truth about which sensors are contributing -----------
  const legend = $("hradar-range");
  if (legend) {
    const on = (data.sensors || []).filter(s => s.ok).map(s => s.name);
    const off = (data.sensors || []).find(s => !s.ok);
    let text = on.length ? on.join(" + ") : (off ? off.detail : "no sensors");
    if (named.length) text += " \u00b7 " + named.map(c => c.label).join(", ") + " by device";
    legend.textContent = text.length > 74 ? text.slice(0, 71) + "..." : text;
  }
}

refreshRadar();
setInterval(refreshRadar, 1000);



