// Ambient HUD layer — read-only telemetry + reacts to Jarvis's state. Never interactive.
const API = "http://127.0.0.1:" + (window.JARVIS_PORT || "8770");
const $ = (id) => document.getElementById(id);

// clock + date
function tick() {
  const n = new Date();
  $("aclock").textContent = n.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("adate").textContent = n.toLocaleDateString([], { weekday: "short", day: "2-digit", month: "short" });
}
tick(); setInterval(tick, 1000);

// telemetry
async function stats() {
  try {
    const d = await (await fetch(API + "/stats")).json();
    if (d && d.cpu_percent != null) {
      set("cpu", "cpubar", Math.round(d.cpu_percent), (d.cpu_temp ? Math.round(d.cpu_temp) + "°" : Math.round(d.cpu_percent) + "%"));
      set("mem", "membar", Math.round(d.mem.percent), Math.round(d.mem.percent) + "%");
      if (d.gpu) set("gpu", "gpubar", d.gpu.util, d.gpu.util + "%");
    }
  } catch (e) {}
}
function set(valId, barId, pct, label) { $(valId).textContent = label; $(barId).style.width = Math.max(3, Math.min(100, pct)) + "%"; }
stats(); setInterval(stats, 3000);

// state reactions from the live event stream
function state(s) {
  document.body.classList.remove("listening", "thinking", "speaking", "idle");
  document.body.classList.add(s || "idle");
  $("astatus").textContent = s === "listening" ? "listening" : s === "thinking" ? "processing" : s === "speaking" ? "responding" : "systems online";
}
try {
  const es = new EventSource(API + "/events");
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (d.kind === "wake") state("listening");
    else if (d.kind === "heard") state("thinking");
    else if (d.kind === "reply") { state("speaking"); setTimeout(() => state(null), Math.min(6000, 1600 + (d.text || "").length * 28)); }
    else if (d.kind === "sleep" || d.kind === "ready") state(null);
  };
} catch (e) {}
