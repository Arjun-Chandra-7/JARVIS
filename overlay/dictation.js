// The dictation capsule: shows what dictation is doing, then gets out of the way.
"use strict";

const API = "http://127.0.0.1:" + (new URLSearchParams(location.search).get("port") || "8770");
const body = document.body;
const label = document.querySelector(".label");
const preview = document.querySelector(".preview");
const bars = Array.from(document.querySelectorAll(".bar"));

const WORDS = {
  listening: "Listening",
  speech: "Listening",
  processing: "Processing",
  inserting: "Inserting",
  inserted: "Inserted",
  inserted_unverified: "Sent — couldn't confirm",
  cancelled: "Cancelled",
  offline: "Offline — using local speech",
  error: "Something went wrong",
  refused: "Not typing there",
  preview: "Check the command",
  confirm: "Confirm?",
  timeout: "Stopped — too long",
  idle: "",
};
// How long a finished state stays on screen before the capsule leaves.
const LINGER = { inserted: 900, inserted_unverified: 2600, cancelled: 900, error: 4200, refused: 3000,
                 offline: 3000, timeout: 2500, idle: 1400 };
let hideTimer = null;

function show(state, message, previewText) {
  clearTimeout(hideTimer);
  body.dataset.state = state === "level" ? body.dataset.state : state;
  label.textContent = message || WORDS[state] || "";
  if (previewText) {
    preview.textContent = previewText;
    preview.hidden = false;
  } else if (state !== "level") {
    preview.hidden = true;
  }
  if (state in LINGER) {
    hideTimer = setTimeout(() => { body.dataset.state = "idle"; preview.hidden = true; }, LINGER[state]);
  }
}

function level(v) {
  const shape = [0.55, 0.85, 1, 0.8, 0.5];
  bars.forEach((b, i) => { b.style.height = Math.max(4, Math.round(4 + 12 * v * shape[i])) + "px"; });
}

function connect() {
  const es = new EventSource(API + "/events");
  es.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.kind !== "dictation") return;
    let d;
    try { d = JSON.parse(msg.text); } catch { return; }
    if (d.state === "level") { if (body.dataset.state === "listening" || body.dataset.state === "speech") level(d.level || 0); return; }
    show(d.state, d.message, d.preview);
  };
  es.onerror = () => { es.close(); setTimeout(connect, 2000); };
}
connect();
