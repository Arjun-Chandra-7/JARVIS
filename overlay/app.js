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
  taskBadge: $("taskBadge"),
  cancelAllBtn: $("cancelAllBtn"),
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

// A voice process that dies mid-turn never sends the event that ends the turn, so without this
// the panel spins on "Working…" indefinitely while the pill says idle. Time out and say so.
const STALL_MS = { listening: 45000, thinking: 180000, speaking: 120000 };
let stallTimer = 0;

function armStallWatchdog(next) {
  clearTimeout(stallTimer);
  const limit = STALL_MS[next];
  if (!limit) return;
  stallTimer = setTimeout(() => {
    if (state.activity !== next) return;
    setActivity("idle", "No response — the voice service may have stopped");
  }, limit);
}

function setActivity(next, detail = "") {
  state.activity = next;
  body.dataset.state = next;
  els.pillState.textContent = detail || STATE_LABEL[next] || next;
  armStallWatchdog(next);

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

// The backend announces the same thing more than once by design: a committed transcript arrives
// as both "transcript" and "heard", and a reply arrives as "reply" and again as "speaking" — once
// per sentence, because speech is streamed sentence by sentence. Each of those was appending its
// own bubble, so every exchange appeared two or three times.
const lastSaid = { you: "", jarvis: "", at: 0 };

// Asking and recording are separate: a caller that checks first and then delegates to addMessage
// would otherwise have its own record read back as a duplicate, and the message would vanish.
function sameAsLast(who, text) {
  return lastSaid[who] === text && Date.now() - lastSaid.at < 30000;
}

function remember(who, text) {
  lastSaid[who] = text;
  lastSaid.at = Date.now();
}

function addMessage(who, text, { kind = "", animate = true } = {}) {
  if (kind !== "partial") {
    if (sameAsLast(who, text)) return null;
    remember(who, text);
  }
  const el = document.createElement("div");
  el.className = `msg ${who}${kind ? " " + kind : ""}`;
  if (!animate) el.style.animation = "none";
  const label = who === "you" ? "You" : who === "jarvis" ? "JARVIS" : "";
  const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  // The side a message sits on already says who spoke, so a label on every line was repeating it.
  // It stays in the accessibility tree, where it is the only signal there is.
  if (label) el.setAttribute("aria-label", `${label} said`);
  el.innerHTML =
    (label ? `<span class="msg-who">${label}</span>` +
             `<span class="msg-time">${time}</span>` : "") +
    `<div class="msg-body">${renderInline(text)}</div>`;
  els.log.appendChild(el);
  while (els.log.children.length > 120) els.log.removeChild(els.log.firstChild);
  autoScroll();
  // A reply that appears all at once reads as a page load; one that arrives in pieces reads as
  // someone answering. Only JARVIS, only when it is the newest thing, and never so slowly that
  // reading has to wait for it.
  if (who === "jarvis" && animate && text.length > 24 && !prefersStill()) reveal(el, text);
  if (who === "jarvis") offerChoices(el, text);
  return el;
}

// A reply that lists what it found used to be a dead end: the names were right there and the
// only way to act on one was to say its title back. They are buttons now.
const _LIST = /(?:Results include|Options|I found|Several (?:visible )?controls match)[:\s]+([^.]{4,300})\./i;

function offerChoices(el, text) {
  const found = _LIST.exec(text || "");
  if (!found) return;
  const items = found[1].split(/,\s*(?![^(]*\))/)
    .map((s) => s.replace(/^(?:and|or)\s+/i, "").trim())
    .filter((s) => s.length > 2 && s.length < 70)
    .slice(0, 6);
  if (items.length < 2) return;

  // What to do with a choice depends on what was being offered.
  const verb = /control|button|click/i.test(text) ? "click" : "open";
  const row = document.createElement("div");
  row.className = "choices";
  row.innerHTML = items
    .map((i) => `<button type="button" class="chip choice" data-say="${escapeHtml(verb)} ${escapeHtml(i)}">${escapeHtml(i)}</button>`)
    .join("");
  el.appendChild(row);
}

els.log.addEventListener("click", (e) => {
  const chip = e.target.closest(".choice");
  if (chip && chip.dataset.say) send(chip.dataset.say);
});

function prefersStill() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function reveal(el, text) {
  const body_ = el.querySelector(".msg-body");
  const words = text.split(/(\s+)/);
  const perTick = Math.max(2, Math.ceil(words.length / 24));   // ~24 frames whatever the length
  let i = 0;
  body_.innerHTML = "";
  el.classList.add("is-revealing");
  const step = () => {
    if (!el.isConnected) return;
    i += perTick;
    body_.innerHTML = renderInline(words.slice(0, i).join(""));
    if (i < words.length) {
      requestAnimationFrame(step);
      if (state.pinnedToBottom) els.log.scrollTop = els.log.scrollHeight;
    } else {
      body_.innerHTML = renderInline(text);
      el.classList.remove("is-revealing");
      autoScroll();
    }
  };
  requestAnimationFrame(step);
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
  if (sameAsLast("you", text)) {
    dropPartial();
    return;
  }
  if (state.partialEl) {
    state.partialEl.classList.remove("partial");
    state.partialEl.querySelector(".msg-body").innerHTML = renderInline(text);
    state.partialEl = null;
    remember("you", text);          // addMessage never ran, so record it here
    autoScroll();
  } else {
    addMessage("you", text);        // which remembers it itself
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
  // No fade at the very top: the first message of a conversation should not look cut off.
  els.log.classList.toggle("at-top", els.log.scrollTop < 4);
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

  // The orb breathes with the same signal, so the pill alone conveys level. It is published as
  // a custom property as well, so the glow and the rings can be driven from CSS rather than
  // every visual effect needing its own line of JavaScript here.
  if (els.orbCore) {
    const scale = 1 + Math.min(0.85, currentLevel * 1.6);
    els.orbCore.style.transform = `scale(${scale.toFixed(3)})`;
    body.style.setProperty("--level", Math.min(1, currentLevel * 2.4).toFixed(3));
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
    body.style.setProperty("--level", "0");
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

function syncSendButton() {
  els.sendBtn.disabled = !els.input.value.trim();
}
els.input.addEventListener("input", syncSendButton);
syncSendButton();

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

async function cancelRunning(button, { alsoIdle = false } = {}) {
  button.disabled = true;
  try {
    const r = await fetch(`${API}/tasks/cancel`, { method: "POST" });
    const j = await r.json();
    // Honest reporting: queued work stops, but something already running may refuse to.
    toast(j.message || "Cancelled");
    if (alsoIdle && j.cancelled) setActivity("idle", "Cancelled");
    refreshTasks();
  } catch {
    toast("Could not cancel — backend unreachable");
  } finally {
    button.disabled = false;
  }
}

els.cancelBtn.addEventListener("click", () => cancelRunning(els.cancelBtn, { alsoIdle: true }));
els.cancelAllBtn.addEventListener("click", () => {
  if (!confirmInline(els.cancelAllBtn, "Cancel all?")) return;
  cancelRunning(els.cancelAllBtn);
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

/** Files a job changed, from the git snapshots the manager takes before and after. */
function changedFiles(job) {
  const before = new Set(((job.before || {}).status || []).map((l) => l.slice(3)));
  const after = ((job.after || {}).status || []).map((l) => l.slice(3));
  const added = after.filter((f) => !before.has(f));
  return { count: added.length, sample: added.slice(0, 4) };
}

function relativeTime(iso) {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return `${Math.round(secs)}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

function renderTasks(jobs) {
  const running = jobs.filter((t) => (t.status || "").match(/running|queued|pending/i)).length;
  els.taskBadge.hidden = running === 0;
  els.taskBadge.textContent = String(running);
  els.workDot.hidden = running === 0;
  els.cancelAllBtn.hidden = running === 0;

  // Everything that ever ran was being listed together, so the tab opened on eight identical
  // "External claude session · cancelled" cards from days ago and read as broken. What is running
  // now is the point of the tab; what has finished is history and belongs behind a heading.
  const isLive = (t) => (t.status || "").match(/running|queued|pending/i);
  const live = jobs.filter(isLive);
  const past = jobs.filter((t) => !isLive(t)).slice(0, 5);

  if (!live.length && !past.length) {
    els.taskList.innerHTML = '<p class="empty">Nothing running, and nothing has run recently.</p>';
    return;
  }

  const section = (label, items, note, render) =>
    !items.length ? "" : `<div class="card-group">
        <div class="group-head"><span>${label}</span>${
          note ? `<span class="group-note">${note}</span>` : ""}</div>
        ${items.map(render).join("")}
      </div>`;

  els.taskList.innerHTML =
    (live.length
      ? section("Running now", live, "", card)
      : '<p class="empty">Nothing running right now.</p>')
    + section("Finished recently", past,
              past.length < jobs.length - live.length
                ? `${jobs.length - live.length - past.length} older hidden` : "",
              pastRow);

  // Finished work is context, not the subject of the tab. A full card each turned six cancelled
  // jobs into a full screen; one line each says the same thing and leaves room for what matters.
  function pastRow(t) {
    const status = (t.status || "unknown").toLowerCase();
    const cls = status.match(/done|complete|finished/) ? "done"
      : status.match(/fail|error/) ? "failed"
      : status.match(/cancel/) ? "cancelled" : "";
    const title = t.prompt || t.task || t.title || t.id || "task";
    const when = relativeTime(t.finished_at || t.started_at || t.created_at);
    return `<div class="past-row">
        <span class="status-dot ${cls}"></span>
        <span class="past-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
        <span class="past-meta">${escapeHtml([t.provider, when].filter(Boolean).join(" · "))}</span>
        <span class="past-status ${cls}">${escapeHtml(status)}</span>
      </div>`;
  }

  function card(t) {
      const status = (t.status || "unknown").toLowerCase();
      const cls = status.match(/running|queued|pending/) ? "running"
        : status.match(/done|complete|finished/) ? "done"
        : status.match(/fail|error/) ? "failed"
        : status.match(/cancel/) ? "cancelled" : "";

      const title = t.prompt || t.task || t.title || t.id || "task";
      const workspace = (t.workspace || "").split("/").filter(Boolean).pop() || "";
      const when = relativeTime(t.finished_at || t.started_at || t.created_at);
      const meta = [t.provider, workspace, when].filter(Boolean).join(" · ");

      const explicitSteps = (t.steps || []).slice(-6).map((s) =>
        `<div class="step ${escapeHtml(s.status || "")}">
           <span class="step-mark">${s.status === "done" ? "✓" : s.status === "failed" ? "✕" : "·"}</span>
           <span>${escapeHtml(s.text || String(s))}</span>
         </div>`).join("");

      // Evidence a person can check: which files the job actually touched.
      const { count, sample } = changedFiles(t);
      const fileSteps = !explicitSteps && count
        ? `<div class="steps">${sample.map((f) =>
             `<div class="step done"><span class="step-mark">✓</span>
                <span>${escapeHtml(f)}</span></div>`).join("")}${
             count > sample.length
               ? `<div class="step"><span class="step-mark">·</span>
                    <span>and ${count - sample.length} more</span></div>`
               : ""}</div>`
        : "";

    return `<article class="card ${cls === "running" ? "is-live" : ""}">
        <div class="card-head">
          <span class="status-dot ${cls}"></span>
          <span class="card-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
          <span class="card-meta">${escapeHtml(status)}</span>
        </div>
        ${meta ? `<div class="card-meta">${escapeHtml(meta)}</div>` : ""}
        ${cls === "running" ? '<div class="progress"></div>' : ""}
        ${explicitSteps ? `<div class="steps">${explicitSteps}</div>` : fileSteps}
        ${t.error ? `<div class="card-body" style="color:var(--error)">${escapeHtml(String(t.error).slice(0, 300))}</div>` : ""}
      </article>`;
  }
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
    // The backend already reports temperatures, totals and GPU memory; a single line of
    // "CPU 20% MEM 32%" threw nearly all of it away and left the tab three-quarters empty.
    const gauges = [];
    const push = (label, pct, detail, invert = false) => {
      if (pct === undefined || pct === null || Number.isNaN(pct)) return;
      const v = Math.max(0, Math.min(100, Math.round(pct)));
      // High is bad for load, good for charge: a full battery must not be drawn as an alarm.
      const band = invert
        ? (v <= 10 ? "hot" : v <= 25 ? "warm" : "")
        : (v >= 90 ? "hot" : v >= 70 ? "warm" : "");
      gauges.push(`<div class="gauge ${band}">
          <div class="gauge-top">
            <span class="gauge-label">${label}</span>
            <span class="gauge-value">${v}<span class="gauge-unit">%</span></span>
          </div>
          <div class="gauge-track"><div class="gauge-fill" style="width:${v}%"></div></div>
          <div class="gauge-detail">${detail ? escapeHtml(detail) : "&nbsp;"}</div>
        </div>`);
    };

    push("CPU", s.cpu_percent,
         [s.cpu_temp ? `${Math.round(s.cpu_temp)}°C` : "",
          (s.load || []).length ? `load ${s.load[0].toFixed(1)}` : ""].filter(Boolean).join(" · "));
    if (s.mem) push("Memory", s.mem.percent, `${s.mem.used_gb} of ${s.mem.total_gb} GB`);
    if (s.gpu) push("GPU", s.gpu.util,
                    [`${s.gpu.mem_used} / ${s.gpu.mem_total} MB`,
                     s.gpu.temp ? `${s.gpu.temp}°C` : ""].filter(Boolean).join(" · "));
    if (s.disk) push("Disk", s.disk.percent, `${s.disk.used_gb} of ${s.disk.total_gb} GB`);
    if (s.battery) push("Battery", s.battery.percent,
                        s.battery.plugged ? "on mains" : (s.battery.mins_left
                          ? `${Math.round(s.battery.mins_left / 60)}h left` : "on battery"),
                        true);

    const uptime = s.uptime_h ? `up ${s.uptime_h.toFixed(1)}h` : "";
    els.telemetry.innerHTML =
      `<div class="gauges">${gauges.join("")}</div>` +
      (uptime ? `<div class="telemetry-foot">${uptime}${
          s.gpu && s.gpu.name ? ` · ${escapeHtml(s.gpu.name)}` : ""}</div>` : "");
  } catch {
    els.telemetry.innerHTML = '<p class="empty">Telemetry unavailable.</p>';
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
      // A state change only. The words arrive as "reply"; adding them here too — once per
      // spoken sentence — is what produced the doubled and tripled replies.
      setActivity("speaking");
      break;
    case "reply":
      if (text) addMessage("jarvis", text);
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
