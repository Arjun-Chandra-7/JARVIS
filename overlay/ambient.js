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
  try {
    // 1. Fetch physical acoustic sonar echoes (real human bodies in room)
    const sonarData = await (await fetch(API + "/sonar")).json().catch(() => null);
    const targets = [];

    if (sonarData && sonarData.humans && sonarData.humans.length > 0) {
      sonarData.humans.forEach((h, idx) => {
        targets.push({
          id: h.id || `body_${idx}`,
          name: `HUMAN ${idx + 1}`,
          type: "PHYSICAL BODY",
          dist: h.distance,
          angle: h.angle_deg || 0.0,
          x_m: h.x_m || 0.0,
          y_m: h.y_m || h.distance,
          source: "sonar",
          conf: h.confidence || 0.8
        });
      });
    }

    targets.sort((a, b) => a.dist - b.dist);
    const count = targets.length;

    if (count === 0) {
      $("nearcount").textContent = "SCANNING";
    } else if (count === 1) {
      $("nearcount").textContent = "1 PERSON";
    } else {
      $("nearcount").textContent = `${count} PEOPLE`;
    }

    const host = $("hblips");
    if (!host) return;

    // Track blip elements by persistent Target ID
    if (!window._blipMap) window._blipMap = new Map();
    const blipMap = window._blipMap;
    const activeIds = new Set(targets.map(t => t.id));

    // Remove old tracks
    for (const [id, el] of blipMap.entries()) {
      if (!activeIds.has(id)) {
        el.remove();
        blipMap.delete(id);
      }
    }

    for (let i = 0; i < targets.length; i++) {
      const t = targets[i];
      let el = blipMap.get(t.id);
      if (!el) {
        el = document.createElement("div");
        el.className = "hblip";
        el.innerHTML = '<span class="htag"></span><span class="hdist"></span>';
        host.appendChild(el);
        blipMap.set(t.id, el);
      }

      // Exact Azimuth angle mapping to X axis (-45° left to +45° right -> 10% to 90% width)
      const clampedAngle = Math.max(-42, Math.min(42, t.angle));
      const xPct = 50 + (clampedAngle / 42) * 38;

      // Anchored directly on the central horizontal Y-axis (50%)
      const yPct = 50;

      el.style.left = xPct.toFixed(1) + "%";
      el.style.top = yPct + "%";

      // Glowing Cyan/Teal HUD Target marker with proximity glow
      el.style.background = "var(--accent)";
      el.style.borderColor = "var(--accent2)";
      el.style.boxShadow = "0 0 14px var(--accent), 0 0 28px var(--accent)";

      // Size scales with physical distance: closer = larger (18px at 0.5m -> 9px at 3.5m)
      const size = Math.max(9, Math.min(18, 20 - (t.dist / 3.5) * 11));
      el.style.width = size + "px";
      el.style.height = size + "px";

      const tagEl = el.querySelector(".htag");
      if (tagEl) {
        if (count === 1) {
          tagEl.textContent = t.dist < 1.8 ? "YOU" : "PERSON";
        } else {
          tagEl.textContent = `PERSON ${i + 1}`;
        }
      }

      const distEl = el.querySelector(".hdist");
      if (distEl) {
        const angleStr = Math.abs(t.angle) >= 2 ? ` (${t.angle > 0 ? '+' : ''}${Math.round(t.angle)}°)` : "";
        distEl.textContent = `${t.dist}m${angleStr}`;
      }
    }
  } catch (e) {
    $("nearcount").textContent = "SONAR ACTIVE";
  }
}

// 400ms polling for smooth continuous locked-target tracking
refreshRadar();
setInterval(refreshRadar, 400);



