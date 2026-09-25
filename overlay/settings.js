// Live settings for the overlay — motion, brightness, visibility — applied, then measured.
//
// "Disable your animations" arrives as a `settings` event on the backend's stream (and is
// fetched once at start-up from GET /settings). It is applied to the document, and then the
// page reports what it is *actually* doing: how many CSS animations are still running, the
// motion mode on <html>, the effective intensity and whether the window is shown. The backend
// only says "animations are off" once that report agrees.
(() => {
  const API = `http://127.0.0.1:${window.JARVIS_PORT || 8770}`;
  const root = document.documentElement;
  const state = { motion: "full", intensity: 1, visible: true, revision: -1 };

  function motionFor(values) {
    if (values["overlay.animations"] === false) return "off";
    if (values["motion.reduced"] === true) return "reduced";
    return "full";
  }

  // Running animations, counted by the engine itself. With motion off, the only ones allowed
  // are none: CSS animations and transitions are suppressed by the stylesheet, and the audio
  // meter's requestAnimationFrame loop checks JarvisSettings.still() and stops drawing.
  function runningAnimations() {
    if (typeof document.getAnimations !== "function") return null;
    return document.getAnimations().filter((a) => a.playState === "running").length;
  }

  async function report(revision, values) {
    // Two frames, so style recalculation has happened before anything is counted.
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const observed = {
      motion: root.dataset.motion || "full",
      running_animations: runningAnimations(),
      intensity: Number(getComputedStyle(root).getPropertyValue("--overlay-intensity")) || 1,
      visible: state.visible,
    };
    try {
      await fetch(`${API}/settings/effective`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ component: "overlay", revision, values, observed }),
      });
    } catch { /* backend away: the change stays applied, and is simply unconfirmed */ }
  }

  function apply(payload) {
    const values = (payload && payload.values) || {};
    const revision = Number(payload && payload.revision);
    if (!Number.isFinite(revision) || revision < state.revision) return;   // stale event
    state.revision = revision;
    state.motion = motionFor(values);
    root.dataset.motion = state.motion;
    if (state.motion === "off") {
      // Anything already mid-flight is finished now rather than left to run out.
      for (const a of document.getAnimations?.() || []) {
        try { a.finish(); } catch { a.cancel(); }
      }
    }
    const intensity = Math.min(1, Math.max(0.3, Number(values["overlay.intensity"]) || 1));
    state.intensity = intensity;
    root.style.setProperty("--overlay-intensity", String(intensity));
    if (typeof values["overlay.visible"] === "boolean" && values["overlay.visible"] !== state.visible) {
      state.visible = values["overlay.visible"];
      if (state.visible) window.jarvis?.show?.();
      else window.jarvis?.hide?.();
    }
    report(revision, values);
  }

  async function load() {
    try {
      const r = await fetch(`${API}/settings`);
      if (r.ok) apply((await r.json()).overlay);
    } catch { /* retried when the stream reconnects */ }
  }

  window.JarvisSettings = {
    apply,
    load,
    // For the page's own animation loops: draw nothing that moves when motion is off.
    still: () => state.motion === "off",
    reduced: () => state.motion !== "full",
    onEvent(kind, text) {
      if (kind !== "settings") return false;
      try { apply(JSON.parse(text)); } catch { /* malformed: ignore */ }
      return true;
    },
  };
  load();
})();
