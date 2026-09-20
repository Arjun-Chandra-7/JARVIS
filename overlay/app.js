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
  pillReply: $("pillReply"),
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
  sendBtn: $("sendBtn"),
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

// The idle breath is the only thing this window animates forever, and measured it costs 27% of a
// core to do it. It runs for this long after the last sign of life and then stops; every path
// that could mean someone is watching calls stir() to start it again.
const SETTLE_AFTER = 20000;
let settleTimer = 0;

function stir() {
  delete body.dataset.settled;
  clearTimeout(settleTimer);
  // Only the idle pill breathes, so only the idle pill needs settling. Listening and speaking
  // stop on their own when the state changes.
  settleTimer = setTimeout(() => { body.dataset.settled = ""; }, SETTLE_AFTER);
}

// Anything that suggests a person is there. Pointer movement is listened for on the window rather
// than the pill because the pill is small and the pointer passes near it far more often than over
// it. Losing focus settles at once — nobody is looking at a background window.
for (const event of ["pointermove", "pointerdown", "keydown", "wheel", "focus"]) {
  window.addEventListener(event, stir, { passive: true });
}
window.addEventListener("blur", () => {
  clearTimeout(settleTimer);
  body.dataset.settled = "";
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") body.dataset.settled = "";
  else stir();
});

function setActivity(next, detail = "") {
  stir();                       // a change of state is the clearest sign of life there is
  // A new question makes the previous answer stale; leaving it up reads as a reply to the thing
  // being asked right now.
  if (next === "listening" || next === "thinking") hideFromPill();
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

// How long an answer stays on the pill: long enough to read it, and no longer. Roughly the time
// it takes to read at a relaxed pace, floored so a two-word answer does not blink past and capped
// so a long one does not sit there all afternoon. The whole answer is always a click away.
function readingTime(text) {
  return Math.min(14000, Math.max(3500, text.length * 55));
}

let replyTimer = 0;

// A reply that made a picture names the file it wrote. Showing the picture is the whole point of
// having asked for one, and a path is not a picture.
//
// Deliberately narrow: only files Jarvis itself writes pictures to. The renderer will happily
// load any file:// image it is given, and a reply is text from a language model — it should not
// be able to name an arbitrary path on the disk and have the window render it.
const PICTURE_RE = /(\/[^\s"'<>]*\/Pictures\/Jarvis\/[^\s"'<>]+\.(?:png|jpe?g|webp))/;

function pictureIn(text) {
  const found = PICTURE_RE.exec(text || "");
  return found ? found[1] : null;
}

// Once the picture itself is on the pill, the path is noise and so is being told it was saved —
// "Made it in 7.7 seconds, sir — saved to 20260916-125534_a_fox.png" says nothing the picture and
// the clock do not. The clause goes; what is left is the part worth reading.
//
// A phrasing this does not recognise keeps its path, shortened to the file name, rather than
// being mangled — an odd-looking reply is better than a wrong one.
function withoutTheLongPath(text, path) {
  const quoted = path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const trimmed = (text || "")
    .replace(new RegExp(`\\s*(?:[—-]|,)?\\s*(?:and\\s+)?saved to\\s+${quoted}`), "")
    .replace(/\s{2,}/g, " ")
    .trim();
  if (!trimmed.includes(path)) return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
  return trimmed.replace(path, path.split("/").pop());
}

// The pill is 280px wide and about twenty-five characters fit on a line, so most answers need
// more room than it has. It is given up to two extra lines — beyond that an answer is something
// to read in the panel, not on a chip — and the pill grows to fit and shrinks back after.
const REPLY_LINE = 18;
const REPLY_MAX_LINES = 3;
const THUMB = 46;               /* .pill-thumb in app.css — the two must agree */

function showOnPill(text) {
  const line = (text || "").replace(/\s+/g, " ").trim();
  clearTimeout(replyTimer);
  if (!line) return hideFromPill();
  const picture = pictureIn(line);
  const shown = picture ? withoutTheLongPath(line, picture) : line;
  els.pillReply.textContent = shown;
  els.pillReply.hidden = false;
  els.pillReply.classList.toggle("has-thumb", Boolean(picture));

  if (picture) {
    // Built as an element with its src assigned, never by pasting a path into markup.
    const img = document.createElement("img");
    img.className = "pill-thumb";
    img.src = `file://${picture}`;
    img.alt = "";
    els.pillReply.prepend(img);
  }

  // Measured, and measured with the thumbnail already in place. The font is proportional, so
  // "Opened Netflix" and "WWWWWWWWWWWWWW" are the same length and nothing like the same width;
  // and a thumbnail floated beside the text takes 46 of the 162 pixels the line had, so a
  // measurement taken before it was inserted clamped a two-word answer down to "Made it in 7.7…".
  els.pillReply.style.whiteSpace = "normal";
  els.pillReply.style.webkitLineClamp = String(REPLY_MAX_LINES);
  const lines = Math.min(REPLY_MAX_LINES,
    Math.max(1, Math.ceil(els.pillReply.scrollHeight / REPLY_LINE)));
  els.pillReply.style.webkitLineClamp = String(lines);

  // The pill has to clear the thumbnail even when the words beside it would have fitted on one.
  const needed = Math.max(lines * REPLY_LINE, picture ? THUMB : 0);
  window.jarvis?.growPill?.(Math.max(0, needed - REPLY_LINE));

  // A frame between unhiding and marking it shown, or the fade has nothing to fade from.
  requestAnimationFrame(() => els.pillReply.setAttribute("data-shown", ""));
  replyTimer = setTimeout(hideFromPill, readingTime(line));
}

function hideFromPill() {
  clearTimeout(replyTimer);
  els.pillReply.removeAttribute("data-shown");
  // The pill shrinks with the fade rather than after it, so the text does not sit in a box that
  // is visibly too big for it on the way out.
  window.jarvis?.growPill?.(0);
  // Left in the layout until the fade finishes, so the pill does not jump as it goes.
  replyTimer = setTimeout(() => { els.pillReply.hidden = true; }, 280);
}

els.pillReply.addEventListener("click", () => {
  setForm("conversation");
  hideFromPill();
});

function addMessage(who, text, { kind = "", animate = true } = {}) {
  if (kind !== "partial") {
    if (sameAsLast(who, text)) return null;
    remember(who, text);
  }
  // An answer you can read without opening anything. Only while collapsed — the panel below is
  // already showing this very message — and never for a partial, which is still being written.
  if (who === "jarvis" && kind !== "partial" && state.form === "pill" && animate) {
    showOnPill(text);
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
             `<span class="msg-time">${time}</span>` +
             `<button class="msg-copy" type="button" title="Copy">copy</button>` : "") +
    `<div class="msg-body">${renderInline(text)}</div>`;
  // The same picture, at a size worth looking at, in the conversation. Appended as an element so
  // the path never passes through innerHTML. Clicking opens it in the system image viewer.
  //
  // Attached to the message rather than to its body: a reply long enough to earn the typing
  // animation has its body's innerHTML rebuilt on every frame of it, which threw the picture
  // away each time. Every reply that makes a picture is long enough, so it never survived.
  const picture = who === "jarvis" ? pictureIn(text) : null;
  if (picture) {
    const img = document.createElement("img");
    img.className = "msg-picture";
    img.src = `file://${picture}`;
    img.alt = text;
    img.title = "Open";
    img.addEventListener("click", () => window.jarvis?.openPicture?.(picture));
    el.appendChild(img);
  }
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

els.log.addEventListener("click", async (e) => {
  const chip = e.target.closest(".choice");
  if (chip && chip.dataset.say) {
    send(chip.dataset.say);
    return;
  }
  const copy = e.target.closest(".msg-copy");
  if (!copy) return;
  const body = copy.closest(".msg")?.querySelector(".msg-body");
  if (!body) return;
  try {
    await navigator.clipboard.writeText(body.innerText);
    copy.textContent = "copied";
    setTimeout(() => { copy.textContent = "copy"; }, 1200);
  } catch {
    copy.textContent = "can't";
    setTimeout(() => { copy.textContent = "copy"; }, 1200);
  }
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

  syncNowPlaying();
}

// ---------------------------------------------------------------- forms
function setForm(name, { fromMain = false } = {}) {
  state.form = name;
  body.dataset.form = name;
  if (!fromMain) window.jarvis.setForm(name);
  if (name !== "pill" && name !== "ironman") {
    // Focus the input only when the user opened the panel themselves. Iron Man mode has no
    // input of its own and stealing focus there would take the keyboard off the editor, which
    // is the one window the whole mode exists to put in front of you.
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
  syncSendButton();
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
  // Only when no coding agent has claimed the dot: an agent working is more interesting than
  // a background job, and two things driving one element flicker between them.
  if (!els.workDot.dataset.agent || els.workDot.dataset.agent === "idle") {
    els.workDot.hidden = running === 0;
  }
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
  els.memoryResults.classList.add("is-searching");
  try {
    const r = await fetch(`${API}/memory/search?q=${encodeURIComponent(q)}`);
    const j = await r.json();
    els.memoryResults.classList.remove("is-searching");
    renderMemory(j.results || []);
  } catch (err) {
    els.memoryResults.classList.remove("is-searching");
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
    // Iron Man mode is decided by the voice, which reaches the backend, which reaches here. The
    // overlay changes shape rather than a second window appearing over the first.
    case "mode":
      setForm(text === "ironman" ? "ironman" : "pill");
      break;
    // The coding agents, watched whether Jarvis started them or you opened them yourself.
    case "agent": {
      const [kind, who] = String(text || "idle").split(":");
      els.workDot.dataset.agent = kind;
      els.workDot.hidden = kind === "idle";
      els.workDot.title = {
        running: `${who || "An agent"} is working`,
        asking: `${who || "An agent"} is waiting for your answer`,
        done: `${who || "An agent"} has finished`,
      }[kind] || "";
      break;
    }
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
  // Ctrl+K opens the list of everything JARVIS can be told to do; Ctrl+Shift+K is the older
  // workspace toggle, which the expand button also does and which nobody had to discover.
  if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
    e.preventDefault();
    if (e.shiftKey) setForm(state.form === "workspace" ? "conversation" : "workspace");
    else togglePalette();
    return;
  }
  // While the palette is up it owns the keyboard: nothing below should also fire, or Escape
  // would close the palette and collapse the panel in the same press.
  if (palette.open) {
    handlePaletteKey(e);
    return;
  }

  // Typing is the commonest thing to want and it needed a click on the right box first.
  // Alt, not a bare number: expanding the panel puts the cursor in the input, so plain 1-4 were
  // unreachable exactly when someone would want them, and typing a digit would have moved the tab.
  if (/^[1-4]$/.test(e.key) && e.altKey && state.form === "workspace") {
    e.preventDefault();
    selectTab(TABS[Number(e.key) - 1]);
    return;
  }
  if (e.key === "/" && !/^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || "")) {
    e.preventDefault();
    if (state.form === "pill") setForm("conversation");
    selectTab("chat");
    els.input.focus();
    return;
  }

  // Escape from the input gives the box up before it gives the panel up: one press to stop
  // typing, another to close. Collapsing mid-sentence loses what was typed.
  if (e.key === "Escape" && document.activeElement === els.input && els.input.value) {
    e.preventDefault();
    els.input.value = "";
    syncSendButton();
    return;
  }

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

// ---------------------------------------------------------------- command palette
//
// A voice assistant you have to already know the words for is a menu with the menu missing.
// Ctrl+K lists what JARVIS can be told to do, filters as you type, and runs it from the keyboard.
//
// Every entry below reaches something that already exists: a capture endpoint, a control
// endpoint, a tab in this window, or a sentence the backend's own command layer parses. Nothing
// here is wired to a stub — an entry with no working destination does not belong in the list.

const palette = {
  open: false,
  items: [],      // what is currently shown, in order
  index: 0,       // which row is highlighted
  asleep: null,   // from /power, so the sleep entry names the thing it will actually do
  suggestions: [],
};

els.palette = $("palette");
els.paletteInput = $("paletteInput");
els.paletteList = $("paletteList");

/** Send a sentence the backend's command layer understands, and show it in the transcript. */
function runPhrase(phrase) {
  send(phrase);
}

function commandList() {
  const speaking = state.activity === "speaking";
  const list = [
    { group: "Capture", label: "Read my screen", note: "attach a screenshot",
      run: () => useLens("screen") },
    { group: "Capture", label: "Explain the selection", note: "attach selected text",
      run: () => useLens("selection") },
    { group: "Capture", label: "Use what is on the clipboard", note: "attach the clipboard",
      run: () => useLens("clipboard") },
    { group: "Capture", label: "Read this window", note: "attach the focused window",
      run: () => useLens("window") },

    { group: "Voice", label: "Stop speaking", note: speaking ? "playing now" : "",
      run: () => els.stopSpeakBtn.click() },
    { group: "Voice", label: "Cancel what is running", note: "stops queued work",
      run: () => cancelRunning(els.cancelBtn, { alsoIdle: true }) },
    { group: "Voice",
      // Named for what pressing it does, which means knowing which way round it currently is.
      label: palette.asleep ? "Listen for the wake word again" : "Stop listening for the wake word",
      note: palette.asleep === null ? "" : (palette.asleep ? "asleep" : "awake"),
      run: () => setPower(palette.asleep ? "wake" : "sleep") },

    { group: "Modes", label: "Study mode", note: "closes the distractions",
      run: () => runPhrase("study mode") },
    { group: "Modes", label: "Leave study mode", note: "", run: () => runPhrase("exit study mode") },
    { group: "Modes", label: "Iron Man mode", note: "lays out the workshop",
      run: () => runPhrase("iron man mode") },
    { group: "Modes", label: "Back to normal mode", note: "restores the desktop",
      run: () => runPhrase("normal mode") },
    { group: "Modes", label: "Open my LinkedIn copilot", note: "",
      run: () => runPhrase("open my linkedin copilot") },

    { group: "Go to", label: "Conversation", note: "Alt+1",
      run: () => { setForm("workspace"); selectTab("chat"); } },
    { group: "Go to", label: "Tasks", note: "Alt+2",
      run: () => { setForm("workspace"); selectTab("tasks"); } },
    { group: "Go to", label: "Memory", note: "Alt+3",
      run: () => { setForm("workspace"); selectTab("memory"); } },
    { group: "Go to", label: "System", note: "Alt+4",
      run: () => { setForm("workspace"); selectTab("system"); } },
    { group: "Go to", label: "Collapse to the pill", note: "Esc", run: () => setForm("pill") },
    { group: "Go to", label: "Hide the overlay", note: "", run: () => window.jarvis.hide() },
  ];
  // What the backend itself suggests, so the list grows when the backend learns something new
  // rather than when this file is edited.
  for (const s of palette.suggestions) {
    if (s && s.label && s.say) list.push({ group: "Ask", label: s.label, note: "", run: () => send(s.say) });
  }
  return list;
}

async function setPower(action) {
  try {
    const r = await fetch(`${API}/power`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const j = await r.json();
    palette.asleep = !!j.asleep;
    toast(j.asleep ? "Not listening for the wake word" : "Listening for the wake word");
  } catch {
    toast("Could not reach the backend");
  }
}

/* Subsequence match, scored so that initials beat letters buried mid-word: typing "rms" should
 * find "Read my screen" rather than whichever entry happens to contain those letters first.
 * Returns null when the query does not match at all. */
function fuzzy(text, query) {
  const q = query.toLowerCase().replace(/\s+/g, "");
  if (!q) return { score: 0, html: escapeHtml(text) };
  const lower = text.toLowerCase();
  let qi = 0;
  let html = "";
  let score = 0;
  let run = 0;
  for (let i = 0; i < text.length; i++) {
    if (qi < q.length && lower[i] === q[qi]) {
      html += `<b>${escapeHtml(text[i])}</b>`;
      const boundary = i === 0 || /[\s\-/]/.test(text[i - 1]);
      // Consecutive letters are weighted hard, because they are the strongest evidence that
      // this is the entry meant: without it "irn" put "Cancel what is running" — i·s, r·unning,
      // ru·n·ning — above "Iron Man mode", which starts with the letters in order.
      score += 1 + run * 3 + (boundary ? 4 : 0) + (i === 0 ? 6 : 0);
      run += 1;
      qi += 1;
    } else {
      html += escapeHtml(text[i]);
      run = 0;
    }
  }
  return qi === q.length ? { score, html } : null;
}

function renderPalette() {
  const query = els.paletteInput.value.trim();
  const matches = [];
  for (const cmd of commandList()) {
    const hit = fuzzy(cmd.label, query);
    if (hit) matches.push({ ...cmd, score: hit.score, html: hit.html });
  }
  // With nothing typed the list keeps its written order, which is grouped and readable. Once
  // something is typed the best match belongs at the top, whatever group it came from.
  if (query) matches.sort((a, b) => b.score - a.score);

  palette.items = matches;
  if (palette.index >= matches.length) palette.index = Math.max(0, matches.length - 1);

  if (!matches.length) {
    els.paletteList.innerHTML =
      `<p class="palette-empty">Nothing matches “${escapeHtml(query)}”. ` +
      `Press Esc and just ask.</p>`;
    return;
  }

  let html = "";
  let group = null;
  matches.forEach((cmd, i) => {
    // Headings only make sense while the list is in its written order; a sorted list is one list.
    if (!query && cmd.group !== group) {
      group = cmd.group;
      html += `<div class="palette-group">${escapeHtml(cmd.group)}</div>`;
    }
    html +=
      `<button type="button" class="palette-item" role="option" data-i="${i}" ` +
      `aria-selected="${i === palette.index}">` +
      `<span class="palette-label">${cmd.html}</span>` +
      (cmd.note ? `<span class="palette-note">${escapeHtml(cmd.note)}</span>` : "") +
      `</button>`;
  });
  els.paletteList.innerHTML = html;
  scrollSelectedIntoView();
}

function scrollSelectedIntoView() {
  const el = els.paletteList.querySelector('[aria-selected="true"]');
  if (el) el.scrollIntoView({ block: "nearest" });
}

function moveSelection(delta) {
  if (!palette.items.length) return;
  // Wraps, because a list you can walk off the end of makes you look at it to use it.
  palette.index = (palette.index + delta + palette.items.length) % palette.items.length;
  for (const el of els.paletteList.querySelectorAll(".palette-item")) {
    el.setAttribute("aria-selected", String(Number(el.dataset.i) === palette.index));
  }
  scrollSelectedIntoView();
}

function runSelected() {
  const cmd = palette.items[palette.index];
  if (!cmd) return;
  closePalette();
  cmd.run();
}

function openPalette() {
  // The palette needs the panel: on the collapsed pill there is nowhere to put it.
  if (state.form === "pill") setForm("conversation");
  palette.open = true;
  palette.index = 0;
  els.palette.hidden = false;
  els.paletteInput.value = "";
  renderPalette();
  els.paletteInput.focus();
  // Both are one cheap request and both change what the list should say, so they are fetched
  // when it opens rather than polled.
  fetch(`${API}/power`).then((r) => r.json()).then((j) => {
    palette.asleep = !!j.asleep;
    if (palette.open) renderPalette();
  }).catch(() => { /* leave the entry unlabelled rather than guessing */ });
  if (!palette.suggestions.length) {
    fetch(`${API}/suggestions`).then((r) => r.json()).then((j) => {
      palette.suggestions = Array.isArray(j.suggestions) ? j.suggestions : [];
      if (palette.open) renderPalette();
    }).catch(() => { /* the built-in entries are the whole list, then */ });
  }
}

function closePalette() {
  palette.open = false;
  els.palette.hidden = true;
  els.paletteInput.blur();
}

function togglePalette() {
  if (palette.open) closePalette();
  else openPalette();
}

function handlePaletteKey(e) {
  switch (e.key) {
    case "Escape": e.preventDefault(); closePalette(); break;
    case "ArrowDown": e.preventDefault(); moveSelection(1); break;
    case "ArrowUp": e.preventDefault(); moveSelection(-1); break;
    case "Tab": e.preventDefault(); moveSelection(e.shiftKey ? -1 : 1); break;
    case "Enter": e.preventDefault(); runSelected(); break;
    default: break;
  }
}

els.paletteInput.addEventListener("input", () => {
  palette.index = 0;
  renderPalette();
});

els.paletteList.addEventListener("click", (e) => {
  const row = e.target.closest(".palette-item");
  if (!row) return;
  palette.index = Number(row.dataset.i);
  runSelected();
});

// Pointer and keyboard move the same highlight, so there is never a second "current" row.
els.paletteList.addEventListener("pointermove", (e) => {
  const row = e.target.closest(".palette-item");
  if (!row || Number(row.dataset.i) === palette.index) return;
  palette.index = Number(row.dataset.i);
  moveSelection(0);
});

// Clicking the dimmed area around the box dismisses it, the way every other sheet does.
els.palette.addEventListener("pointerdown", (e) => {
  if (e.target === els.palette) closePalette();
});

// ---------------------------------------------------------------- what is playing
//
// The backend has been able to see Spotify for a long time; the overlay never said so, which
// meant reaching past an always-on-top window to skip a track.
//
// Polled, not streamed, because playerctl has nothing to subscribe to — so it is polled slowly
// and only while the panel is open and on screen. The rule in DESIGN.md applies here as much as
// to an animation: nothing new is always on.
const NOWPLAYING_EVERY_MS = 5000;
let nowPlayingTimer = 0;
let nowPlayingTrack = null;   // the last title seen, so the art only reloads when it changes

els.nowPlaying = $("nowPlaying");
els.npArt = $("npArt");
els.npTitle = $("npTitle");
els.npArtist = $("npArtist");

const PLAY_GLYPH = '<path d="M7 4l12 8-12 8z" />';
const PAUSE_GLYPH = '<path d="M7 4v16M17 4v16" />';

async function pollNowPlaying() {
  let j;
  try {
    j = await (await fetch(`${API}/spotify`, { cache: "no-store" })).json();
  } catch {
    // Backend unreachable: say nothing rather than leaving a stale track on screen.
    els.nowPlaying.hidden = true;
    return;
  }
  // Nothing loaded in the player at all. A paused track still counts as something to show —
  // that is exactly when the play button is wanted.
  if (!j || (!j.title && !j.playing)) {
    els.nowPlaying.hidden = true;
    nowPlayingTrack = null;
    return;
  }
  els.nowPlaying.hidden = false;
  els.nowPlaying.dataset.playing = String(!!j.playing);
  els.npTitle.textContent = j.title || "";
  els.npArtist.textContent = j.artist || "";
  $("npPlay").querySelector("svg").innerHTML = j.playing ? PAUSE_GLYPH : PLAY_GLYPH;

  if (j.title !== nowPlayingTrack) {
    nowPlayingTrack = j.title;
    // https only. The art URL comes from whatever is playing, and a renderer this privileged
    // should not be talked into fetching file:// or anything else by a track's metadata.
    const art = typeof j.art === "string" && /^https:\/\//.test(j.art) ? j.art : "";
    els.npArt.hidden = !art;
    if (art) els.npArt.src = art;
  }
}

async function musicControl(command) {
  try {
    await fetch(`${API}/spotify/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: command }),
    });
  } catch {
    toast("Could not reach the player");
    return;
  }
  // playerctl takes a moment to settle; asking immediately reports the state before the change.
  setTimeout(pollNowPlaying, 350);
}

$("npPrev").addEventListener("click", () => musicControl("previous"));
$("npNext").addEventListener("click", () => musicControl("next"));
$("npPlay").addEventListener("click", () => musicControl("play-pause"));

/** Runs with the panel, stops with it. Called from syncLoops. */
function syncNowPlaying() {
  const want = state.visible && state.form !== "pill" && state.form !== "ironman";
  if (want && !nowPlayingTimer) {
    pollNowPlaying();
    nowPlayingTimer = setInterval(pollNowPlaying, NOWPLAYING_EVERY_MS);
  }
  if (!want && nowPlayingTimer) {
    clearInterval(nowPlayingTimer);
    nowPlayingTimer = 0;
  }
}

// ---------------------------------------------------------------- the quick chips
//
// Four chips were written into the page by hand while the backend was already publishing the
// list it wants offered. They disagreed, and the hand-written pair was the stale one.
//
// The two capture chips stay in the markup: they are not suggestions, they are the two lenses,
// and they must work whether or not the backend answers. Everything after them comes from
// /suggestions, and if that fetch fails the page keeps exactly what it shipped with.
const QUICK_CHIP_LIMIT = 3;

/* Words, not characters. "Read screen" and the "Read my screen" lens are the same button twice,
 * and neither string contains the other — only the words give it away. */
const chipWords = (s) => new Set(s.toLowerCase().match(/[a-z]+/g) || []);
const sameButton = (a, b) => {
  const [small, big] = a.size <= b.size ? [a, b] : [b, a];
  return small.size > 0 && [...small].every((w) => big.has(w));
};

function renderQuickChips() {
  const offered = palette.suggestions.filter((s) => s && s.label && s.say);
  if (!offered.length) return;

  const lenses = [...els.quick.querySelectorAll("[data-lens]")];
  const taken = lenses.map((b) => chipWords(b.textContent));

  const chosen = [];
  for (const s of offered) {
    const words = chipWords(s.label);
    if (taken.some((t) => sameButton(t, words))) continue;
    taken.push(words);
    chosen.push(s);
    if (chosen.length === QUICK_CHIP_LIMIT) break;
  }
  if (!chosen.length) return;

  for (const b of els.quick.querySelectorAll("[data-say]")) b.remove();
  els.quick.insertAdjacentHTML(
    "beforeend",
    chosen
      .map((s) => `<button type="button" class="chip" data-say="${escapeHtml(s.say)}">` +
                  `${escapeHtml(s.label)}</button>`)
      .join(""),
  );
}

async function loadSuggestions() {
  try {
    const j = await (await fetch(`${API}/suggestions`)).json();
    palette.suggestions = Array.isArray(j.suggestions) ? j.suggestions : [];
  } catch {
    return;   // the markup's own chips are the whole list, then
  }
  renderQuickChips();
}

loadSuggestions();
