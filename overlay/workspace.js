// The Study and 3D tabs, and the "waiting for your yes" strip they share.
//
// Study: the running session, the quiz question on screen (never its answer), practice estimates
// (labelled as indicators), and a box that sends a study turn through the same path as speech.
// 3D: the current job's state, mode, honesty label and version; updated from `studio` events
// without polling, so a long reconstruction never blocks the voice or the page.
// Approvals: summaries from /approvals; a specific approval (naming a provider) is never a button —
// the strip shows the words to say instead. Nothing here is logged to the console.
(() => {
  const API = `http://127.0.0.1:${window.JARVIS_PORT || 8770}`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);

  async function call(path, opts = {}) {
    const r = await fetch(API + path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await r.json(); } catch { /* empty */ }
    if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`);
    return data;
  }

  // ------------------------------------------------------------------ approvals
  async function approvals(slot) {
    const el = $(slot);
    if (!el) return;
    let pending = [];
    try { pending = (await call("/approvals")).pending || []; } catch { el.hidden = true; return; }
    el.hidden = !pending.length;
    el.innerHTML = pending.length ? `<h3 class="w-h">Waiting for your yes</h3><ul class="w-approvals">${pending.map((a) => `
      <li class="b-row"><span class="grow">${esc(a.summary)}</span>
        ${a.say ? `<span class="b-faint">Say “${esc(a.say)}”</span>`
                : `<button class="ghost-btn b-primary" type="button" data-approve="${esc(a.id)}" data-fp="${esc(a.fingerprint)}"
                     aria-label="Approve: ${esc(a.summary)}">Approve</button>`}
        <button class="ghost-btn" type="button" data-cancel="${esc(a.id)}" aria-label="Cancel: ${esc(a.summary)}">Cancel</button>
      </li>`).join("")}</ul>` : "";
  }

  async function decide(id, decision, fingerprint, slot) {
    try {
      const r = await call(`/approvals/${encodeURIComponent(id)}`, { method: "POST", body: { decision, fingerprint } });
      status(slot, r.message || "");
    } catch (e) { status(slot, e.message, true); }
    approvals(slot);
  }

  function status(slot, text, error = false) {
    const el = $(slot.replace("Approvals", "Status"));
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-error", !!error);
  }

  function wireApprovals(slot) {
    $(slot)?.addEventListener("click", (e) => {
      const ok = e.target.closest("[data-approve]");
      const no = e.target.closest("[data-cancel]");
      if (ok) decide(ok.dataset.approve, "confirm", ok.dataset.fp, slot);
      if (no) decide(no.dataset.cancel, "cancel", "", slot);
    });
  }

  // ------------------------------------------------------------------ Study
  const Study = {
    busy: false,
    async refresh() {
      approvals("studyApprovals");
      let s;
      try { s = await call("/study/state"); } catch (e) { status("studyApprovals", e.message, true); return; }
      const sess = s.session;
      $("studySession").innerHTML = !s.enabled
        ? `<p class="b-muted">The Study Companion is off (Daily Brain disabled).</p>`
        : sess ? `<div class="b-row"><span class="b-dot ${sess.paused ? "warn" : "ok"}" aria-hidden="true"></span>
            <strong>${esc(sess.subject || "Study")}${sess.chapter ? " · " + esc(sess.chapter.split(".").pop().replace(/_/g, " ")) : ""}</strong>
            <span class="b-tag">${sess.paused ? "paused" : "running"}</span>${sess.mode ? `<span class="b-tag">${esc(sess.mode.replace(/_/g, " "))}</span>` : ""}
            ${s.diagram_on_screen ? '<span class="b-tag ok">diagram on screen</span>' : ""}</div>`
               : `<p class="b-muted">No study session. Say or type “start a science study session”.</p>`;
      $("studyQuiz").innerHTML = s.quiz ? `<h3 class="w-h">Question ${esc(s.quiz.number)}</h3>
          <p>${esc(s.quiz.prompt)}</p>${s.quiz.options.length ? `<ol class="w-options" type="a">${s.quiz.options.map((o) => `<li>${esc(o)}</li>`).join("")}</ol>` : ""}`
        : `<p class="b-faint">No quiz question waiting. “Quiz me on electricity” starts one.</p>`;
      $("studyProgress").innerHTML = s.progress.length ? `<h3 class="w-h">Practice so far</h3>
          <ul class="w-progress">${s.progress.map((p) => `<li><span class="grow">${esc(p.topic)}</span>
            <meter min="0" max="1" value="${esc(p.estimate)}" aria-label="${esc(p.topic)} practice estimate">${Math.round(p.estimate * 100)}%</meter>
            <span class="b-faint">${Math.round(p.estimate * 100)}% · ${esc(p.evidence)} answers</span></li>`).join("")}</ul>
          <p class="b-faint">${esc(s.note || "A practice indicator, not a formal assessment.")}</p>`
        : `<p class="b-faint">Nothing practised yet.</p>`;
    },
    async ask(text) {
      if (!text.trim() || Study.busy) return;
      Study.busy = true;
      $("studyReply").textContent = "Thinking…";
      try { $("studyReply").textContent = (await call("/study/ask", { method: "POST", body: { text } })).reply; }
      catch (e) { $("studyReply").textContent = e.message; }
      Study.busy = false;
      Study.refresh();
    },
  };

  // ------------------------------------------------------------------ 3D
  const Studio = {
    async refresh() {
      approvals("studioApprovals");
      let s;
      try { s = await call("/3d/status"); } catch (e) { status("studioApprovals", e.message, true); return; }
      $("studioJob").innerHTML = !s.active ? `<p class="b-muted">${esc(s.summary)} Say “make a 3D model of this”.</p>`
        : `<div class="b-row"><span class="b-dot ${s.state === "ready" ? "ok" : s.state === "failed" ? "bad" : "warn"}" aria-hidden="true"></span>
             <strong>${esc(s.state.replace(/_/g, " "))}</strong>${s.mode ? `<span class="b-tag">${esc(s.mode)}</span>` : ""}
             ${s.fidelity ? `<span class="b-tag">${esc(s.fidelity.replace(/_/g, " "))}</span>` : ""}
             ${s.version ? `<span class="b-tag">version ${esc(s.version)}</span>` : ""}</div>
           <p class="b-muted">${esc(s.summary)}</p>
           ${s.question ? `<p><strong>Needs:</strong> ${esc(s.question)}</p>` : ""}
           ${s.stages.length ? `<p class="b-faint">Done: ${esc(s.stages.join(", "))}</p>` : ""}`;
    },
    onEvent(text) {
      const el = $("studioLive");
      if (el) el.textContent = text;
      if ($("view-studio") && !$("view-studio").hidden) Studio.refresh();
    },
  };

  function init() {
    wireApprovals("studyApprovals");
    wireApprovals("studioApprovals");
    const form = $("studyForm");
    form?.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = $("studyInput");
      const text = input.value;
      input.value = "";
      Study.ask(text);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  window.JarvisStudy = Study;
  window.JarvisStudio = Studio;
})();
