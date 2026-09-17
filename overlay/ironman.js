/* Iron Man mode — live data behind the frame.
 *
 * Everything shown here is real: the projects are the folders in your Dev directory, the gauges
 * are this machine's, the song is whatever Spotify is actually playing. A HUD full of invented
 * numbers is a screensaver, and the point of this one is that it is worth glancing at.
 */

// Wrapped, because this shares a global scope with app.js and both want the obvious names. Two
// `const $` declarations in one scope is a SyntaxError that throws out the entire file — silently,
// as far as the page is concerned: the script tag is there, every symbol in it is undefined, and
// the HUD simply never appears.
(function () {


const API = window.JARVIS_API || "http://127.0.0.1:8770";
// Every id in this file is namespaced; see ironman.html for why.
const $ = (id) => document.getElementById(`im-${id}`);

async function getJSON(path) {
  try {
    const r = await fetch(`${API}${path}`, { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

/* ---------------------------------------------------------------- clock */
function tick() {
  const now = new Date();
  $("date").textContent = now.toLocaleDateString("en-GB", {
    weekday: "short", day: "2-digit", month: "short", year: "numeric",
  }).toUpperCase().replace(/,/g, "");
  $("time").textContent = now.toLocaleTimeString("en-GB", {
    hour: "2-digit", minute: "2-digit", hour12: true,
  }).toUpperCase();
}

/* ---------------------------------------------------------------- projects */
const PROJECT_GLYPH = ["◆", "◈", "◇", "▣", "▤", "▥", "▨", "◉", "⬡", "⬢"];

async function loadProjects() {
  const data = await getJSON("/projects");
  const list = $("projects");
  list.innerHTML = "";
  const items = (data && data.projects) || [];
  items.slice(0, 9).forEach((p, i) => {
    const row = document.createElement("div");
    row.className = "row";
    row.title = p.path || p.name;
    row.innerHTML = `<span class="dot">${PROJECT_GLYPH[i % PROJECT_GLYPH.length]}</span>`;
    row.appendChild(document.createTextNode(p.name));
    // Opening the folder is the thing you actually want from a list of projects — and the
    // terminal goes with it, which the backend does because it is the side that can type.
    row.addEventListener("click", async () => {
      row.classList.add("opening");
      try {
        await fetch(`${API}/project/open`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: p.path }),
        });
      } catch { /* the HUD is not the place to complain about it */ }
      setTimeout(() => row.classList.remove("opening"), 1200);
    });
    list.appendChild(row);
  });
  const all = document.createElement("div");
  all.className = "row muted";
  all.textContent = `${items.length} PROJECTS`;
  list.appendChild(all);
}

/* ---------------------------------------------------------------- gauges */
const GAUGES = [
  { key: "cpu", label: "CPU" },
  { key: "gpu", label: "GPU" },
  { key: "ram", label: "RAM" },
  { key: "disk", label: "DISK" },
];
const CIRC = 2 * Math.PI * 16;

function drawGauges(stats) {
  const box = $("gauges");
  if (!box.dataset.built) {
    box.innerHTML = GAUGES.map(g => `
      <div class="gauge">
        <svg viewBox="0 0 40 40">
          <circle class="ring-bg" cx="20" cy="20" r="16"/>
          <circle class="ring-fg" id="im-ring-${g.key}" cx="20" cy="20" r="16"
                  stroke-dasharray="0 ${CIRC}"/>
          <text class="ring-label" x="20" y="23" id="im-pct-${g.key}">–</text>
        </svg>
        <div class="meta"><b>${g.label}</b><span id="im-val-${g.key}">–</span></div>
      </div>`).join("");
    box.dataset.built = "1";
  }
  GAUGES.forEach(g => {
    const v = Math.max(0, Math.min(100, Number(stats?.[g.key] ?? 0)));
    $(`ring-${g.key}`).setAttribute("stroke-dasharray", `${(v / 100) * CIRC} ${CIRC}`);
    $(`pct-${g.key}`).textContent = `${Math.round(v)}%`;
    $(`val-${g.key}`).textContent = `${Math.round(v)}%`;
  });
}

// The endpoint reports what the machine measures — cpu_percent, mem.percent, disk.percent, a gpu
// block when there is a card — and the gauges want four plain numbers. Mapped here rather than
// renaming anything server-side, because those names are shared with the other views.
function asPercents(s) {
  const gpu = s.gpu || {};
  return {
    cpu: s.cpu_percent,
    gpu: gpu.util ?? gpu.util_percent ?? 0,
    ram: s.mem?.percent,
    disk: s.disk?.percent,
  };
}

async function loadStats() {
  const s = await getJSON("/stats");
  if (s) {
    drawGauges(asPercents(s));
    const pct = s.battery?.percent;
    if (typeof pct === "number") {
      $("batPct").textContent = `${Math.round(pct)}%`;
      $("batBar").style.width = `${Math.round(pct)}%`;
    }
  }
}

/* ---------------------------------------------------------------- now playing */
function wave(el, seed, points = 60) {
  // A waveform that looks like sound rather than a sine: a few harmonics beaten together.
  const pts = [];
  for (let i = 0; i < points; i++) {
    const t = i / points;
    const a = Math.sin(t * 22 + seed) * Math.sin(t * 7 + seed * 1.7);
    pts.push(`${(i / (points - 1)) * 120},${15 + a * 12}`);
  }
  el.setAttribute("points", pts.join(" "));
}

async function loadSong() {
  const s = await getJSON("/spotify");
  const box = $("playing");
  const playing = s && (s.title || s.artist);
  box.hidden = !playing;
  if (!playing) return;
  $("songTitle").textContent = s.title || "—";
  $("songArtist").textContent = s.artist || "—";
  $("play").textContent = s.status === "Playing" ? "❚❚" : "▶";
  wave($("songWave"), Date.now() / 900);
}

function wireSong() {
  const send = (what) => fetch(`${API}/spotify/control`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: what }),
  }).then(() => setTimeout(loadSong, 400)).catch(() => {});
  $("prev").onclick = () => send("previous");
  $("next").onclick = () => send("next");
  $("play").onclick = () => send("playpause");
}

/* ---------------------------------------------------------------- right rail */
const QUICK = [
  { name: "ChatGPT", glyph: "◎", url: "https://chatgpt.com/" },
  { name: "Claude", glyph: "✳", url: "https://claude.ai/" },
  { name: "GitHub", glyph: "⬢", url: "https://github.com/" },
  { name: "Notion", glyph: "▤", url: "https://notion.so/" },
  { name: "Google", glyph: "◉", url: "https://google.com/" },
  { name: "YouTube", glyph: "▶", url: "https://youtube.com/" },
  { name: "Terminal", glyph: "▣", app: "terminal" },
];

const AGENTS = [
  { name: "Coder", glyph: "✦" },
  { name: "Researcher", glyph: "◈" },
  { name: "Analyst", glyph: "▦" },
  { name: "Operator", glyph: "◆" },
];

function buildRail() {
  const quick = $("quick");
  quick.innerHTML = "";
  QUICK.forEach(q => {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span class="dot">${q.glyph}</span>`;
    row.appendChild(document.createTextNode(q.name));
    row.addEventListener("click", () => {
      if (q.url) window.jarvis?.openExternal?.(q.url);
      else window.jarvis?.openTerminal?.();
    });
    quick.appendChild(row);
  });

  const agents = $("agents");
  agents.innerHTML = "";
  AGENTS.forEach(a => {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span class="dot">${a.glyph}</span>`;
    row.appendChild(document.createTextNode(a.name));
    agents.appendChild(row);
  });
}

/* ---------------------------------------------------------------- pulse + modes */
function drawPulse() {
  const pts = [];
  for (let i = 0; i < 80; i++) {
    const t = i / 80;
    // A flat line with a heartbeat in it, rather than noise all the way across.
    const spike = Math.exp(-Math.pow((t - 0.5) * 14, 2)) * Math.sin(t * 60);
    pts.push(`${t * 200},${20 - spike * 16}`);
  }
  $("pulse").setAttribute("points", pts.join(" "));
}

function wireModes() {
  $("modes").addEventListener("click", (e) => {
    const button = e.target.closest("button");
    if (!button) return;
    [...$("modes").children].forEach(b => b.classList.toggle("on", b === button));
  });
}

/* ---------------------------------------------------------------- click-through
 * The frame covers the whole screen, so without this the editor underneath would never get a
 * click again. The window ignores the pointer by default and stops ignoring it only while the
 * pointer is actually over a panel — worked out from what is under the cursor rather than from
 * rectangles, so it stays right however the layout changes.
 */
function wireClickThrough() {
  let through = true;
  const solid = (el) => !!el && !!el.closest(".frame, .reactor");
  window.addEventListener("mousemove", (e) => {
    const over = solid(document.elementFromPoint(e.clientX, e.clientY));
    if (over === !through) return;
    through = !over;
    window.jarvis?.setClickThrough?.(through);
  }, { passive: true });
}

/* ---------------------------------------------------------------- start */
let running = false;
let timers = [];

function stopHud() {
  timers.forEach(clearInterval);
  timers = [];
  running = false;
  delete document.body.dataset.armed;
  window.jarvis?.setClickThrough?.(false);
}

// The markup lives in ironman.html and is pulled in the first time the mode is armed, so there
// is exactly one copy of it.
let loaded = false;

async function loadFrame() {
  if (loaded) return true;
  const host = document.getElementById("ironmanHud");
  if (!host) return false;
  try {
    const r = await fetch("ironman.html", { cache: "no-store" });
    host.innerHTML = await r.text();
    loaded = true;
    return true;
  } catch {
    return false;
  }
}

async function start() {
  if (running) return;
  if (!await loadFrame()) return;
  running = true;
  document.body.classList.add("ironman");
  document.body.dataset.armed = "";
  wireClickThrough();
  drawPulse();
  buildRail();
  wireSong();
  wireModes();
  tick();
  await Promise.all([loadProjects(), loadStats(), loadSong()]);
  timers = [
    setInterval(tick, 1000),
    setInterval(loadStats, 4000),
    setInterval(loadSong, 5000),
    setInterval(loadProjects, 60000),
  ];
}

/* The HUD exists in the same page as the pill and the panel, and runs only while the mode is on:
 * four polling loops left running behind a hidden element would be the same mistake as the
 * breathing orb, and this one polls the network. */
function followTheForm() {
  const hud = document.getElementById("ironmanHud");
  const armed = document.body.dataset.form === "ironman";
  if (hud) hud.hidden = !armed;
  document.body.classList.toggle("ironman", armed);
  if (armed) start();
  else if (running) stopHud();
}

const watchForm = new MutationObserver(followTheForm);
watchForm.observe(document.body, { attributes: true, attributeFilter: ["data-form"] });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", followTheForm);
else followTheForm();
})();
