// JARVIS overlay renderer.
//
// Principles this file holds to:
//   * Every visible state comes from a real backend event or a real fetch. Nothing animates to
//     suggest activity that is not happening — the previous waveform was synthesised from a sine
//     wave and moved whether or not a microphone was open.
//   * Animation loops stop when the window is hidden or the state is idle. An overlay that sits
//     on screen all day must cost nothing when it is doing nothing.
//   * The transcript distinguishes what JARVIS is still hearing (partial) from what it committed.

const API = `http://127.0.0.1:${window.JARVIS_PORT || 8770}`;

const $ = (id) => document.getElementById(id);
const body = document.body;

const els = {
  shell: $("shell"),
  orb: $("orb"),
  orbCore: document.querySelector(".orb-core"),
  pillState: $("pillState"),
  meter: $("meter"),
  workDot: $("workDot"),
  expandBtn: $("expandBtn"),
  collapseBtn: $("collapseBtn"),
  tabs: $("tabs"),
  log: $("log"),
  scrollPin: $("scrollPin"),
  scrollPinBtn: $("scrollPinBtn"),
  activity: $("activity"),
  activityText: $("activityText"),
  stopSpeakBtn: $("stopSpeakBtn"),
  cancelBtn: $("cancelBtn"),
  composer: $("composer"),
  input: $("input"),
  quick: $("quick"),
  contextChip: $("contextChip"),
  contextKind: $("contextKind"),
  contextPreview: $("contextPreview"),
  contextClear: $("contextClear"),
  taskList: $("taskList"),
  taskEmpty: $("taskEmpty"),
  taskBadge: $("taskBadge"),
  memorySearch: $("memorySearch"),
  memoryQuery: $("memoryQuery"),
  memoryResults: $("memoryResults"),
  systems: $("systems"),
  telemetry: $("telemetry"),
  eventDump: $("eventDump"),
  shortcutInput: $("shortcutInput"),
  shortcutSave: $("shortcutSave"),
  shortcutStatus: $("shortcutStatus"),
  toast: $("toast"),
};

const state = {
  form: "pill",
  visible: true,
  activity: "idle", // idle | listening | thinking | speaking | failed | offline
  busy: false,
  connected: false,
  pinnedToBottom: true,
  partialEl: null,
  context: null, // { kind, text }
  reduceMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
};

// ---------------------------------------------------------------- state machine
const STATE_LABEL = {
  idle: 'Say "Hey JARVIS"',
  listening: "Listening…",
  thinking: "Working…",
  speaking: "Speaking…",
  failed: "Something failed",
  offline: "Backend offline",
  connecting: "Connecting…",
};

function setActivity(next, detail = "") {
  state.activity = next;
  body.dataset.state = next;
  els.pillState.textContent = detail || STATE_LABEL[next] || next;

  const working = next === "thinking";
  const speaking = next === "speaking";
  els.activity.hidden = !(working || speaking);
  els.stopSpeakBtn.hidden = !speaking;
  els.cancelBtn.hidden = !working;
  if (working && !detail) els.activityText.textContent = "Working…";
  else if (detail) els.activityText.textContent = detail;

  syncLoops();
}

// ---------------------------------------------------------------- transcript
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function renderInline(text) {
  // Backend text is data, not markup. Escape first, then allow only `code` spans.
  return escapeHtml(text).replace(/`([^`]+)`/g, "<code>$1</code>");
}

function addMessage(who, text, { kind = "", animate = true } = {}) {
  const el = document.createElement("div");
  el.className = `msg ${who}${kind ? " " + kind : ""}`;
  if (!animate) el.style.animation = "none";
  const label = who === "you" ? "You" : who === "jarvis" ? "JARVIS" : "";
  el.innerHTML =
    (label ? `<div class="msg-who">${label}</div>` : "") +
    `<div class="msg-body">${renderInline(text)}</div>`;
  els.log.appendChild(el);
  while (els.log.children.length > 120) els.log.removeChild(els.log.firstChild);
  autoScroll();
  return el;
}

/** Show or update the provisional transcript. Replaced wholesale when the real one lands. */
function setPartial(text) {
  if (!text) return;
  if (!state.partialEl) {
    state.partialEl = addMessage("you", text, { kind: "partial" });
  } else {
    state.partialEl.querySelector(".msg-body").innerHTML = renderInline(text);
    autoScroll();
  }
}

function commitTranscript(text) {
  if (state.partialEl) {
    state.partialEl.classList.remove("partial");
    state.partialEl.querySelector(".msg-body").innerHTML = renderInline(text);
    state.partialEl = null;
    autoScroll();
  } else {
    addMessage("you", text);
  }
}

function dropPartial() {
  if (state.partialEl) {
    state.partialEl.remove();
    state.partialEl = null;
  }
}

/** Evidence of what actually ran, attached to the reply it produced. */
function attachEvidence(entries) {
  if (!entries.length) return;
  const last = els.log.querySelector(".msg.jarvis:last-of-type");
  if (!last) return;
  const d = document.createElement("details");
  d.className = "evidence";
  d.innerHTML =
    `<summary>${entries.length} tool call${entries.length > 1 ? "s" : ""}</summary>` +
    entries
      .map((e) => `<div class="evidence-item ${escapeHtml(e.cls || "")}">${escapeHtml(e.text)}</div>`)
      .join("");
  last.appendChild(d);
  autoScroll();
}

// Scrolling respects someone reading older messages instead of yanking them to the bottom.
function autoScroll() {
  if (state.pinnedToBottom) {
    els.log.scrollTop = els.log.scrollHeight;
    els.scrollPin.hidden = true;
  } else {
    els.scrollPin.hidden = false;
  }
}
els.log.addEventListener("scroll", () => {
  const gap = els.log.scrollHeight - els.log.scrollTop - els.log.clientHeight;
  state.pinnedToBottom = gap < 40;
  if (state.pinnedToBottom) els.scrollPin.hidden = true;
});
els.scrollPinBtn.addEventListener("click", () => {
  state.pinnedToBottom = true;
  autoScroll();
  els.log.focus();
});

// ---------------------------------------------------------------- audio meter (real levels)
const meterCtx = els.meter.getContext("2d");
const BARS = 36;
const history = new Array(BARS).fill(0);
let meterRaf = 0;
let levelTimer = 0;
let currentLevel = 0;
let targetLevel = 0;

function drawMeter() {
  meterRaf = requestAnimationFrame(drawMeter);
  // Ease toward the most recent real reading so 20 Hz samples look continuous at 60 fps.
  currentLevel += (targetLevel - currentLevel) * 0.25;
  history.push(currentLevel);
  history.shift();

  const { width: w, height: h } = els.meter;
  meterCtx.clearRect(0, 0, w, h);
  const accent = getComputedStyle(body).getPropertyValue("--accent").trim() || "#7ec8e3";
  const gap = w / BARS;
  const mid = h / 2;
  meterCtx.fillStyle = accent;
  for (let i = 0; i < BARS; i++) {
    const envelope = Math.sin((i / (BARS - 1)) * Math.PI); // taper at both ends
    const amp = history[i] * envelope;
    const barH = Math.max(1.5, amp * (h - 3));
    meterCtx.globalAlpha = 0.28 + 0.72 * Math.min(1, amp * 2.2);
    meterCtx.fillRect(i * gap + 0.5, mid - barH / 2, Math.max(1, gap - 1.5), barH);
  }
  meterCtx.globalAlpha = 1;

  // The orb breathes with the same signal, so the pill alone conveys level.
  if (els.orbCore) {
    const scale = 1 + Math.min(0.85, currentLevel * 1.6);
    els.orbCore.style.transform = `scale(${scale.toFixed(3)})`;
  }
}

async function pollLevel() {
  try {
    const r = await fetch(`${API}/audio`, { cache: "no-store" });
    const j = await r.json();
    targetLevel = j.stale ? 0 : Number(j.level) || 0;
  } catch {
    targetLevel = 0;
  }
}

/** Only run the meter when audio is actually flowing and the window is on screen. */
function syncLoops() {
  const audioActive = state.activity === "listening" || state.activity === "speaking";
  const wantMeter = state.visible && audioActive;

  if (wantMeter && !meterRaf) drawMeter();
  if (!wantMeter && meterRaf) {
    cancelAnimationFrame(meterRaf);
    meterRaf = 0;
    targetLevel = 0;
    currentLevel = 0;
    history.fill(0);
    meterCtx.clearRect(0, 0, els.meter.width, els.meter.height);
    if (els.orbCore) els.orbCore.style.transform = "";
  }

  if (wantMeter && !levelTimer) levelTimer = setInterval(pollLevel, 50);
  if (!wantMeter && levelTimer) {
    clearInterval(levelTimer);
    levelTimer = 0;
  }
}

// ---------------------------------------------------------------- forms
function setForm(name, { fromMain = false } = {}) {
  state.form = name;
  body.dataset.form = name;
  if (!fromMain) window.jarvis.setForm(name);
  if (name !== "pill") {
    // Focus the input only when the user opened the panel themselves.
    requestAnimationFrame(() => els.input.focus({ preventScroll: true }));
  }
  syncLoops();
}

els.orb.addEventListener("click", () => {
  setForm(state.form === "pill" ? "conversation" : "pill");
});
els.expandBtn.addEventListener("click", () => setForm("workspace"));
els.collapseBtn.addEventListener("click", () => setForm("pill"));

// ---------------------------------------------------------------- tabs
const TABS = ["chat", "tasks", "memory", "system"];
function selectTab(name) {
  for (const t of TABS) {
    const tab = $(`tab-${t}`);
    const view = $(`view-${t}`);
    if (!tab || !view) continue;
    const active = t === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    view.classList.toggle("is-active", active);
    view.hidden = !active;
  }
  if (name === "tasks") refreshTasks();
  if (name === "system") refreshHealth();
}
els.tabs.addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (tab) selectTab(tab.id.replace("tab-", ""));
});
// Arrow-key navigation between tabs, as a tablist should have.
els.tabs.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const visibleTabs = TABS.filter((t) => $(`tab-${t}`).offsetParent !== null);
  const current = visibleTabs.findIndex((t) => $(`tab-${t}`).classList.contains("is-active"));
  const next = (current + (e.key === "ArrowRight" ? 1 : -1) + visibleTabs.length) % visibleTabs.length;
  $(`tab-${visibleTabs[next]}`).focus();
  selectTab(visibleTabs[next]);
  e.preventDefault();
});

// ---------------------------------------------------------------- sending
async function send(text) {
  const message = (text || "").trim();
  if (!message || state.busy) return;
  state.busy = true;

  if (state.form === "pill") setForm("conversation");

  let payload = message;
  if (state.context) {
    // The attached context is shown to the user verbatim and sent scoped, clearly fenced.
    payload =
      `${message}\n\n[${state.context.kind} the user is referring to]\n` +
      `${state.context.text}\n[end of ${state.context.kind}]`;
    addMessage("you", `${message}\n\n(${state.context.kind} attached)`);
    clearContext();
  } else {
    addMessage("you", message);
  }

  setActivity("thinking");
  const started = performance.now();
  try {
    const r = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: payload }),
    });
    if (!r.ok) throw new Error(`backend returned ${r.status}`);
    const j = await r.json();
    addMessage("jarvis", j.reply || "(no reply)");
    if (Array.isArray(j.tools) && j.tools.length) {
      attachEvidence(j.tools.map((t) => ({ text: `${t.name} ${t.detail || ""}`.trim() })));
    }
    setActivity("idle", `Answered in ${((performance.now() - started) / 1000).toFixed(1)}s`);
    setTimeout(() => state.activity === "idle" && setActivity("idle"), 2600);
  } catch (err) {
    addMessage("sys", `Could not reach the backend: ${err.message}`, { kind: "error" });
    setActivity("offline");
  } finally {
    state.busy = false;
  }
}

els.composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const v = els.input.value;
  els.input.value = "";
  send(v);
});

els.quick.addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.dataset.say) return void send(btn.dataset.say);
  if (btn.dataset.lens) return void useLens(btn.dataset.lens);
});

// ---------------------------------------------------------------- context lens
function showContext(kind, text) {
  state.context = { kind, text };
  els.contextKind.textContent = kind;
  els.contextPreview.textContent = text.replace(/\s+/g, " ").slice(0, 160);
  els.contextChip.hidden = false;
  els.input.focus();
}
function clearContext() {
  state.context = null;
  els.contextChip.hidden = true;
}
els.contextClear.addEventListener("click", clearContext);

async function useLens(kind) {
  if (state.form === "pill") setForm("conversation");
  try {
    const r = await fetch(`${API}/lens/${kind}`, { method: "POST" });
    const j = await r.json();
    if (!j.ok) {
      toast(j.error || "Nothing to capture");
      return;
    }
    showContext(j.kind, j.text);
  } catch (err) {
    toast(`Capture failed: ${err.message}`);
  }
}

// ---------------------------------------------------------------- stop vs cancel
// Deliberately separate: stopping the voice must not abandon the work that produced it.
els.stopSpeakBtn.addEventListener("click", async () => {
  els.stopSpeakBtn.disabled = true;
  try {
    await fetch(`${API}/speech/stop`, { method: "POST" });
    setActivity("idle", "Stopped speaking");
  } catch {
    toast("Could not stop playback");
  } finally {
    els.stopSpeakBtn.disabled = false;
  }
});

els.cancelBtn.addEventListener("click", async () => {
  els.cancelBtn.disabled = true;
  try {
    const r = await fetch(`${API}/tasks/cancel`, { method: "POST" });
    const j = await r.json();
    // Honest reporting: queued work stops, but something already running may not be interruptible.
    toast(j.message || "Cancelled");
    if (j.cancelled) setActivity("idle", "Cancelled");
  } catch {
    toast("Could not cancel");
  } finally {
    els.cancelBtn.disabled = false;
  }
});

// ---------------------------------------------------------------- tasks
async function refreshTasks() {
  try {
    const r = await fetch(`${API}/coding/jobs`);
    const j = await r.json();
    const jobs = (j.jobs || j || []).slice(0, 20);
    renderTasks(Array.isArray(jobs) ? jobs : []);
  } catch {
    els.taskList.innerHTML = '<p class="empty">Task list unavailable — backend offline.</p>';
  }
}

function renderTasks(jobs) {
  const running = jobs.filter((t) => (t.status || "").match(/running|queued|pending/i)).length;
  els.taskBadge.hidden = running === 0;
  els.taskBadge.textContent = String(running);
  els.workDot.hidden = running === 0;

  if (!jobs.length) {
    els.taskList.innerHTML = '<p class="empty">Nothing running.</p>';
    return;
  }
  els.taskList.innerHTML = jobs
    .map((t) => {
      const status = (t.status || "unknown").toLowerCase();
      const cls = status.match(/running|queued|pending/) ? "running"
        : status.match(/done|complete|finished/) ? "done"
        : status.match(/fail|error/) ? "failed"
        : status.match(/cancel/) ? "cancelled" : "";
      const steps = (t.steps || []).slice(-6).map((s) =>
        `<div class="step ${escapeHtml(s.status || "")}">
           <span class="step-mark">${s.status === "done" ? "✓" : s.status === "failed" ? "✕" : "·"}</span>
           <span>${escapeHtml(s.text || s)}</span>
         </div>`).join("");
      return `<article class="card">
        <div class="card-head">
          <span class="status-dot ${cls}"></span>
          <span class="card-title">${escapeHtml(t.task || t.title || t.id || "task")}</span>
          <span class="card-meta">${escapeHtml(status)}</span>
        </div>
        ${cls === "running" ? '<div class="progress"></div>' : ""}
        ${steps ? `<div class="steps">${steps}</div>` : ""}
        ${t.result ? `<div class="card-body">${escapeHtml(String(t.result).slice(0, 400))}</div>` : ""}
      </article>`;
    })
    .join("");
}

// ---------------------------------------------------------------- memory
els.memorySearch.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = els.memoryQuery.value.trim();
  if (!q) return;
  els.memoryResults.innerHTML = '<p class="empty">Searching…</p>';
  try {
    const r = await fetch(`${API}/memory/search?q=${encodeURIComponent(q)}`);
    const j = await r.json();
    renderMemory(j.results || []);
  } catch (err) {
    els.memoryResults.innerHTML = `<p class="empty">Search failed: ${escapeHtml(err.message)}</p>`;
  }
});

function renderMemory(results) {
  if (!results.length) {
    els.memoryResults.innerHTML = '<p class="empty">Nothing stored matches that.</p>';
    return;
  }
  els.memoryResults.innerHTML = results
    .map((m, i) => `<article class="card" data-path="${escapeHtml(m.path || "")}">
        <div class="card-head">
          <span class="card-title">${escapeHtml(m.title || m.path || "note")}</span>
          <span class="card-meta">${escapeHtml(m.kind || "note")}</span>
        </div>
        <div class="card-body">${escapeHtml(m.snippet || "")}</div>
        <div class="card-meta">${escapeHtml(m.source || m.path || "")}${
          m.recorded ? " · " + escapeHtml(m.recorded) : ""}</div>
        <div class="card-actions">
          <button class="ghost-btn" data-act="correct" data-i="${i}">Correct…</button>
          <button class="ghost-btn danger" data-act="forget" data-i="${i}">Forget</button>
        </div>
      </article>`)
    .join("");
  els.memoryResults.dataset.payload = JSON.stringify(results);
}

els.memoryResults.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const results = JSON.parse(els.memoryResults.dataset.payload || "[]");
  const item = results[Number(btn.dataset.i)];
  if (!item) return;

  if (btn.dataset.act === "forget") {
    if (!confirmInline(btn, "Forget?")) return;
    await memoryWrite("/memory/forget", { path: item.path, snippet: item.snippet });
  } else {
    const replacement = window.prompt("What is the correct version?", item.snippet || "");
    if (replacement === null) return;
    await memoryWrite("/memory/correct", {
      path: item.path, snippet: item.snippet, replacement,
    });
  }
  els.memorySearch.dispatchEvent(new Event("submit"));
});

// A two-step confirm that does not open a modal dialog over the desktop.
function confirmInline(btn, label) {
  if (btn.dataset.armed === "1") return true;
  const original = btn.textContent;
  btn.dataset.armed = "1";
  btn.textContent = label;
  setTimeout(() => {
    btn.dataset.armed = "0";
    btn.textContent = original;
  }, 2600);
  return false;
}

async function memoryWrite(path, payload) {
  try {
    const r = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const j = await r.json();
    toast(j.message || (j.ok ? "Updated" : "Failed"));
  } catch (err) {
    toast(`Failed: ${err.message}`);
  }
}

// ---------------------------------------------------------------- health
async function refreshHealth() {
  try {
    const h = await (await fetch(`${API}/health`)).json();
    els.systems.innerHTML = (h.systems || [])
      .map((s) => `<div class="system">
          <span class="status-dot ${s.ok ? "done" : "failed"}"></span>
          <span class="system-name">${escapeHtml(s.name)}</span>
          <span class="system-label">${escapeHtml(s.label || "")}</span>
        </div>`)
      .join("");
  } catch {
    els.systems.innerHTML = '<p class="empty">Backend offline.</p>';
  }
  try {
    const s = await (await fetch(`${API}/stats`)).json();
    const bits = [`<b>CPU</b> ${Math.round(s.cpu_percent)}%`];
    if (s.mem) bits.push(`<b>MEM</b> ${Math.round(s.mem.percent)}%`);
    if (s.gpu) bits.push(`<b>GPU</b> ${s.gpu.util}%`);
    if (s.battery) bits.push(`<b>BAT</b> ${s.battery.percent}%${s.battery.plugged ? " ⚡" : ""}`);
    els.telemetry.innerHTML = bits.join("");
  } catch {
    els.telemetry.textContent = "telemetry unavailable";
  }
}

// ---------------------------------------------------------------- backend events
const recentEvents = [];
let sse = null;
let reconnectDelay = 1000;

function connect() {
  try {
    sse?.close();
  } catch { /* not open */ }
  sse = new EventSource(`${API}/events`);

  sse.onopen = () => {
    state.connected = true;
    reconnectDelay = 1000;
    if (state.activity === "offline") setActivity("idle");
  };

  sse.onmessage = (ev) => {
    let d;
    try {
      d = JSON.parse(ev.data);
    } catch {
      return;
    }
    handleEvent(d.kind, d.text || "");
  };

  sse.onerror = () => {
    state.connected = false;
    setActivity("offline");
    try { sse.close(); } catch { /* already closed */ }
    // Back off so a backend that is down does not get hammered, and say so meanwhile.
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
  };
}

function handleEvent(kind, text) {
  recentEvents.push(`${new Date().toLocaleTimeString()}  ${kind}  ${text}`);
  while (recentEvents.length > 60) recentEvents.shift();
  if (els.eventDump) els.eventDump.textContent = recentEvents.slice(-40).join("\n");

  switch (kind) {
    case "ready":
      setActivity("idle");
      break;
    case "wake":
    case "listening":
      dropPartial();
      setActivity("listening");
      break;
    case "partial":
      setPartial(text);
      break;
    case "transcript":
    case "heard":
      if (text && text !== "(nothing captured)") commitTranscript(text);
      else dropPartial();
      setActivity("thinking");
      break;
    case "speaking":
      setActivity("speaking");
      if (text) addMessage("jarvis", text);
      break;
    case "reply":
      addMessage("jarvis", text);
      break;
    case "spoken":
      if (state.activity === "speaking") setActivity("idle");
      break;
    case "sleep":
      setActivity("idle");
      break;
    case "timing":
      if (els.activityText && state.activity === "thinking") els.activityText.textContent = text;
      break;
    case "phone":
      addMessage("sys", `📱 ${text}`);
      toast(text);
      break;
    case "loading":
      setActivity(state.activity === "idle" ? "idle" : state.activity, text);
      break;
    default:
      break;
  }
}

// ---------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  // Escape always steps back: clear context, then collapse, then hide.
  if (e.key === "Escape") {
    if (state.context) return void clearContext();
    if (state.form !== "pill") return void setForm("pill");
    return void window.jarvis.hide();
  }
  if (e.key === "Tab" && state.form === "pill") {
    // Opening the panel on Tab makes the pill keyboard-reachable rather than a dead end.
    setForm("conversation");
    e.preventDefault();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    setForm(state.form === "workspace" ? "conversation" : "workspace");
    e.preventDefault();
  }
});

// ---------------------------------------------------------------- toast
let toastTimer;
function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.remove("show"), 3200);
}
window.jarvis.onToast(toast);
window.jarvis.onForm((name) => setForm(name, { fromMain: true }));
window.jarvis.onVisibility((vis) => {
  state.visible = vis;
  syncLoops();
  if (vis) refreshHealth();
});

// ---------------------------------------------------------------- settings
els.shortcutSave.addEventListener("click", async () => {
  const accel = els.shortcutInput.value.trim();
  const res = await window.jarvis.setShortcut("toggle", accel);
  els.shortcutStatus.textContent = res.ok ? "Saved." : `Not saved — ${res.error}`;
  els.shortcutStatus.style.color = res.ok
    ? "var(--success)" : "var(--error)";
});

// ---------------------------------------------------------------- boot
(async () => {
  const cfg = await window.jarvis.getState();
  setForm(cfg.form || "pill", { fromMain: true });
  els.shortcutInput.value = cfg.shortcuts?.toggle || "";

  setActivity("connecting");
  connect();
  refreshHealth();
  setInterval(() => {
    if (state.visible) refreshHealth();
  }, 10000);
  setInterval(() => {
    if (state.visible) refreshTasks();
  }, 6000);

  try {
    const msgs = (await (await fetch(`${API}/history?limit=10`)).json()).messages || [];
    for (const m of msgs) addMessage(m.who === "you" ? "you" : "jarvis", m.text, { animate: false });
  } catch {
    /* no history yet */
  }
  refreshTasks();
})();
