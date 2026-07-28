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
function place(el, angle, radiusPct) {
  const R = 50, r = Math.min(46, radiusPct * 0.46);
  el.style.left = (R + Math.cos(angle) * r) + "%";
  el.style.top = (R + Math.sin(angle) * r) + "%";
}
async function nearby() {
  try {
    const n = await (await fetch(API + "/nearby")).json();
    if (n.error) return;
    const c = n.counts || { wifi: 0, lan: 0, bt: 0 };
    $("lgwifi").textContent = c.wifi; $("lglan").textContent = c.lan; $("lgbt").textContent = c.bt;
    $("nearcount").textContent = (c.wifi + c.lan + c.bt) + " seen";
    const frag = [];
    // WiFi: radius from signal (strong = near centre); outer band
    (n.wifi || []).forEach((w) => {
      const rad = 100 - Math.min(95, w.signal);           // strong signal → small radius
      frag.push(blip("wifi", hashAngle("w" + w.ssid), 40 + rad * 0.55, w.current ? w.ssid : ""));
    });
    // LAN devices: mid ring
    (n.lan || []).forEach((d, i) => {
      frag.push(blip("lan", hashAngle("l" + (d.mac || d.ip)), d.router ? 12 : 55, d.router ? "router" : ""));
    });
    // Bluetooth: inner if connected / by RSSI, phones bigger + labelled
    (n.bt || []).forEach((b) => {
      const rad = b.rssi != null ? Math.min(95, Math.max(10, 100 + b.rssi)) : (b.connected ? 20 : 70);
      frag.push(blip("bt" + (b.icon === "phone" ? " phone" : ""), hashAngle("b" + b.mac), rad, b.connected || b.icon === "phone" ? b.name : ""));
    });
    $("blips").innerHTML = frag.join("");
    document.querySelectorAll("#blips .blip").forEach((el) => {
      place(el, parseFloat(el.dataset.a), parseFloat(el.dataset.r));
    });
  } catch (e) {}
}
function blip(cls, angle, radiusPct, label) {
  const tag = label ? `<span class="tag">${esc(label)}</span>` : "";
  return `<div class="blip ${cls}" data-a="${angle}" data-r="${radiusPct}">${tag}</div>`;
}
nearby(); setInterval(nearby, 12000);

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
  };
} catch (e) {}
