// The Brain tab — which models answer, with which keys, under which rules.
//
// Talks only to the backend's /brain API. A saved key never comes back from it: after "Save" the
// field is emptied and the page holds nothing but the key's id, label and a six-character
// fingerprint of its hash. Nothing here is logged to the console.
(() => {
  const API = `http://127.0.0.1:${window.JARVIS_PORT || 8770}/brain`;
  const VIEWS = ["overview", "providers", "routing", "local", "usage"];
  const ROUTES = ["chat", "study", "research", "vision", "tools"];
  const st = { view: "overview", confirm: {}, adding: {} };
  let root, body, status;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const dot = (h) => `<span class="b-dot ${/^ok/.test(h) ? "ok" : /paus|degrad|backoff|untested|unknown/.test(h || "") ? "warn" : /unavail|quarant|unreach|disabled|fail/.test(h || "") ? "bad" : ""}" aria-hidden="true"></span>`;

  function say(text, error = false) {
    status.textContent = text || "";
    status.classList.toggle("is-error", !!error);
  }

  async function call(path, opts = {}) {
    const r = await fetch(API + path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await r.json(); } catch { /* empty body */ }
    if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`);
    return data;
  }

  // ------------------------------------------------------------------ overview
  async function overview() {
    const [o, s] = await Promise.all([call("/overview"), call("/setup").catch(() => null)]);
    const setup = s && s.needs_setup ? `
      <section class="b-card" aria-labelledby="b-setup-h">
        <h3 id="b-setup-h">Set up the brain</h3>
        <p class="b-muted">No model can answer everyday questions yet. Commands like “open YouTube” still work.</p>
        <p class="b-faint">${esc(s.hardware.ram_gb ? `This computer: ${s.hardware.ram_gb} GB RAM${s.hardware.gpu ? `, ${s.hardware.gpu} (${s.hardware.vram_gb} GB)` : ""}.` : "")}
          ${s.local.reachable ? `Ollama is running with ${s.local.models.length} model(s).` : "Ollama is not running."}</p>
        <ol class="b-list">${s.recommendation.map((t) => `<li class="b-muted">• ${esc(t)}</li>`).join("")}</ol>
        <div class="b-row"><button class="ghost-btn b-primary" type="button" data-go="providers">Add a provider key</button></div>
      </section>` : "";
    const routes = ROUTES.map((r) => {
      const v = o.routes[r] || {};
      return `<div class="b-route">
        <div class="b-row">${dot(v.ok ? "ok" : "fail")}<strong>${esc(r)}</strong></div>
        ${v.ok ? `<span class="b-mono">${esc(v.model)}</span>
          <span class="b-faint">${v.fallbacks.length ? "then " + esc(v.fallbacks.join(", ")) : "no fallback configured"}</span>`
               : `<span class="b-faint">${esc(v.why)}</span>`}
      </div>`;
    }).join("");
    const warn = o.warnings.length ? `<ul class="b-warnings">${o.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "";
    const recent = o.recent.length ? `<div class="b-table-wrap"><table class="b-table"><caption class="b-faint" style="text-align:left">Recent routing decisions (no message text is kept)</caption>
      <thead><tr><th>Route</th><th>Model</th><th>Result</th><th>ms</th></tr></thead><tbody>
      ${o.recent.map((r) => `<tr><td>${esc(r.route)}</td><td class="b-mono">${esc(r.provider ? `${r.provider}/${r.model}` : "—")}</td>
        <td>${esc(r.status || "")}${r.fallback ? ' <span class="b-tag warn">fallback</span>' : ""}</td><td>${esc(r.latency_ms ?? "")}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="b-empty">No routed requests yet.</div>`;
    return `${setup}
      <section class="b-card" aria-labelledby="b-ov-h">
        <h3 id="b-ov-h">Routing <span class="b-tag cloud">${esc(o.profile)} profile</span>
          ${o.offline ? '<span class="b-tag warn">offline</span>' : ""}</h3>
        <div class="b-routes">${routes}</div>
        ${warn}
      </section>
      <section class="b-card" aria-labelledby="b-prov-h">
        <h3 id="b-prov-h">Providers</h3>
        <div class="b-row">${o.providers.map((p) => `<span class="b-tag ${p.local ? "local" : "cloud"}">${dot(p.enabled ? p.health : "disabled")} ${esc(p.name)} · ${esc(p.enabled ? p.health : "off")}</span>`).join(" ")}</div>
        <p class="b-faint">${o.usage.events} requests recorded · fallback rate ${(o.usage.fallback_rate * 100).toFixed(0)}%</p>
      </section>
      <section class="b-card">${recent}</section>`;
  }

  // ------------------------------------------------------------------ providers
  function keyRow(p, k) {
    const s = k.status || {};
    const conf = st.confirm[k.id];
    const env = k.source === "env";
    return `<li class="b-key" data-pid="${esc(p.id)}" data-kid="${esc(k.id)}">
      ${dot(s.state === "ok" ? "ok" : s.state)}
      <span class="grow"><span>${esc(k.label)}</span>
        <span class="b-faint"> · #${esc(k.fingerprint)} · ${esc(k.storage)} · ${esc(s.state || "")}${s.reason ? " (" + esc(s.reason) + ")" : ""}${s.seconds ? ` ${esc(s.seconds)}s` : ""}</span></span>
      <button class="ghost-btn" type="button" data-act="key-up" aria-label="Move ${esc(k.label)} up">↑</button>
      <button class="ghost-btn" type="button" data-act="key-down" aria-label="Move ${esc(k.label)} down">↓</button>
      <button class="ghost-btn" type="button" data-act="key-test">Test</button>
      <button class="ghost-btn" type="button" data-act="key-toggle">${k.enabled ? "Disable" : "Enable"}</button>
      ${env ? `<button class="ghost-btn" type="button" data-act="key-migrate" title="Copies it into the system keyring; .env is not changed">Move to keyring</button>`
            : `<button class="ghost-btn" type="button" data-act="key-rename">Rename</button>
               ${conf ? `<button class="ghost-btn danger" type="button" data-act="key-delete-confirm">Confirm remove</button>
                         <button class="ghost-btn" type="button" data-act="key-delete-cancel">Keep</button>`
                      : `<button class="ghost-btn danger" type="button" data-act="key-delete">Remove</button>`}`}
    </li>`;
  }

  function providerCard(p) {
    const models = p.models.map((m) => {
      const ver = Object.entries(m.verified || {}).filter(([, v]) => v.ok).map(([c]) => c);
      const cls = !m.available ? "bad" : m.health.startsWith("paused") ? "warn" : m.health === "ok" ? "ok" : "";
      return `<span class="b-tag ${cls}" title="${esc(m.declared.join(", "))}">${esc(m.id)}${ver.length ? " ✓ " + esc(ver.join(", ")) : ""}${m.deprecated ? " · deprecated" : ""}${!m.available ? " · removed" : ""}</span>
        ${!m.available && m.suggested_replacement ? `<button class="ghost-btn" type="button" data-act="replace" data-ref="${esc(m.ref)}" data-new="${esc(m.suggested_replacement)}">Use ${esc(m.suggested_replacement)}?</button>` : ""}
        ${(m.declared.includes("tool_calling") || m.declared.includes("vision")) ? `<button class="ghost-btn" type="button" data-act="validate" data-ref="${esc(m.ref)}" title="A few tiny test requests; nothing is executed">Validate</button>` : ""}`;
    }).join(" ");
    const keyed = !(p.local && p.auth === "none");
    const adding = st.adding[p.id];
    return `<section class="b-card" aria-labelledby="bp-${esc(p.id)}" data-pid="${esc(p.id)}">
      <h3 id="bp-${esc(p.id)}">${dot(p.enabled ? p.health : "disabled")}${esc(p.display_name)}
        <span class="b-tag ${p.local ? "local" : "cloud"}">${esc(p.privacy)}</span>
        <span class="grow"></span>
        <label class="b-switch"><input type="checkbox" data-act="toggle" ${p.enabled ? "checked" : ""}> Enabled</label></h3>
      ${p.last_failure ? `<p class="b-faint">Last failure: ${esc(p.last_failure)}</p>` : ""}
      ${p.base_url_editable ? `<div class="b-row"><input class="input mono grow" data-field="base" aria-label="Base URL for ${esc(p.display_name)}" value="${esc(p.base_url)}" spellcheck="false"><button class="ghost-btn" type="button" data-act="base">Save URL</button></div>` : `<p class="b-faint b-mono">${esc(p.base_url)}</p>`}
      ${keyed ? `<h4>Keys <span class="b-faint">stored in ${esc(p.key_storage)}</span></h4>
        ${p.keys.length ? `<ul class="b-list">${p.keys.map((k) => keyRow(p, k)).join("")}</ul>` : `<div class="b-empty">No key yet.</div>`}
        ${adding ? `<form class="b-form" data-act="key-save" autocomplete="off">
            <div class="b-grid2">
              <label class="b-field">API key<input class="input mono" type="password" name="secret" autocomplete="new-password" spellcheck="false" placeholder="${esc(p.key_hint || "paste key")}" required></label>
              <label class="b-field">Label<input class="input" type="text" name="label" maxlength="40" placeholder="e.g. Personal"></label>
            </div>
            <div class="b-row"><button class="ghost-btn b-primary" type="submit">Save key</button><button class="ghost-btn" type="button" data-act="key-add-cancel">Cancel</button></div>
          </form>` : `<div class="b-row"><button class="ghost-btn" type="button" data-act="key-add" aria-label="Add a key for ${esc(p.display_name)}">+ Add key</button></div>`}` : ""}
      <h4>Models</h4>
      <div class="b-models">${models || '<span class="b-faint">none configured</span>'}</div>
      <div class="b-row">
        <input class="input mono grow" data-field="model" aria-label="Model id to add for ${esc(p.display_name)}" placeholder="model id" spellcheck="false">
        <button class="ghost-btn" type="button" data-act="model-add">Add model</button>
        <button class="ghost-btn" type="button" data-act="test">Test connection</button>
      </div>
      ${p.discovered.length ? `<details><summary class="b-faint">${p.discovered.length} models offered by the provider</summary><p class="b-mono">${esc(p.discovered.join("  "))}</p></details>` : ""}
    </section>`;
  }

  async function providers() {
    const d = await call("/providers");
    st.providers = d.providers;
    const tmpl = Object.entries(d.templates).map(([k, v]) => `<option value="${esc(k)}">${esc(v.display_name)}</option>`).join("");
    return d.providers.map(providerCard).join("") + `
      <section class="b-card" aria-labelledby="b-addp-h"><h3 id="b-addp-h">Add a provider</h3>
        <form class="b-form" data-act="provider-add">
          <div class="b-grid2">
            <label class="b-field">Type<select name="template">${tmpl}</select></label>
            <label class="b-field">Id (optional)<input class="input mono" name="id" maxlength="32" placeholder="e.g. work-openai"></label>
          </div>
          <label class="b-field">Base URL (for self-hosted or compatible endpoints)<input class="input mono" name="base_url" placeholder="https://…/v1"></label>
          <div class="b-row"><button class="ghost-btn b-primary" type="submit">Add provider</button></div>
        </form></section>`;
  }

  // ------------------------------------------------------------------ routing
  async function routing() {
    const r = await call("/routing");
    st.routing = r;
    const prof = Object.entries(r.profiles).map(([k, v]) => `<label class="b-switch" title="${esc(v)}"><input type="radio" name="profile" value="${esc(k)}" ${k === r.profile ? "checked" : ""}> ${esc(k)}</label>`).join(" ");
    const ov = (r.overrides[r.profile] || {});
    const opts = (sel) => `<option value="">automatic</option>` + r.models.map((m) => `<option ${m === sel ? "selected" : ""}>${esc(m)}</option>`).join("");
    const rows = r.routes.map((route) => `<div class="b-grid2">
        <label class="b-field">${esc(route)} — primary<select data-route="${esc(route)}" data-slot="0">${opts((ov[route] || [])[0])}</select></label>
        <label class="b-field">${esc(route)} — fallback<select data-route="${esc(route)}" data-slot="1">${opts((ov[route] || [])[1])}</select></label>
      </div>`).join("");
    const pv = r.privacy, lim = r.limits;
    return `<form class="b-card b-form" data-act="routing-save" aria-labelledby="b-rt-h">
        <h3 id="b-rt-h">Profile</h3>
        <fieldset class="b-row" style="border:0;padding:0;margin:0"><legend class="b-faint">Hover a profile for what it does</legend>${prof}</fieldset>
        <h4>Order for the “${esc(r.profile)}” profile</h4>${rows}
        <h4>Limits</h4>
        <div class="b-grid2">
          <label class="b-field">Max cost per request (USD)<input type="number" step="0.001" min="0" name="max_cost_usd" value="${esc(lim.max_cost_usd)}"></label>
          <label class="b-field">Max latency (seconds)<input type="number" step="1" min="1" name="max_latency_s" value="${esc(lim.max_latency_s)}"></label>
        </div>
        <label class="b-switch"><input type="checkbox" name="cloud_escalation" ${lim.cloud_escalation ? "checked" : ""}> Allow escalation to the strongest cloud model</label>
        <label class="b-switch"><input type="checkbox" name="external_vision" ${lim.external_vision ? "checked" : ""}> Allow images to be sent to cloud vision models</label>
        <h4>Privacy</h4>
        <label class="b-field">What may go to cloud models<select name="mode">
          ${[["allow_cloud", "Allow configured cloud"], ["prefer_local", "Prefer local"], ["ask_before_cloud", "Ask before cloud (private items stay local)"], ["always_local", "Always local"]]
            .map(([v, t]) => `<option value="${v}" ${pv.mode === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <label class="b-switch"><input type="checkbox" name="allow_screenshots" ${pv.allow_screenshots ? "checked" : ""}> Screenshots may be uploaded</label>
        <p class="b-faint">Passwords, OTPs and keys never go to a cloud model, whatever is set here.</p>
        <div class="b-row"><button class="ghost-btn b-primary" type="submit">Save routing</button></div>
      </form>`;
  }

  // ------------------------------------------------------------------ local
  async function local() {
    const d = await call("/local");
    const hw = d.hardware || {};
    const hwLine = `${hw.ram_gb ?? "?"} GB RAM (${hw.ram_available_gb ?? "?"} free)${hw.gpu ? ` · ${hw.gpu}, ${hw.vram_gb} GB VRAM (${hw.vram_used_gb} used)` : " · no GPU found"}`;
    if (!d.reachable) return `<section class="b-card"><h3>Local models</h3><p class="b-muted">${esc(hwLine)}</p><div class="b-empty">Ollama is not running, so nothing local can answer. Start it with <code>ollama serve</code>.</div></section>`;
    const rows = d.models.map((m) => {
      const conf = st.confirm["local:" + m.name];
      return `<tr data-model="${esc(m.name)}">
        <td class="b-mono">${esc(m.name)}${m.large ? ' <span class="b-tag warn">large</span>' : ""}</td>
        <td>${esc(m.size_gb)} GB<br><span class="b-faint">~${esc(m.ram_gb)} GB to run</span></td>
        <td>${m.loaded ? `<span class="b-tag ok">loaded</span>${m.vram_now_gb ? ` <span class="b-faint">${esc(m.vram_now_gb)} GB VRAM</span>` : ""}` : '<span class="b-tag">idle</span>'}</td>
        <td class="b-faint">${esc(m.recommended_for.join("; "))}${m.benchmark ? `<br>passed: ${esc(m.benchmark.passed.join(", ") || "none")}${m.benchmark.warm_first_token_ms ? ` · ${esc(m.benchmark.warm_first_token_ms)} ms first token` : ""}` : ""}</td>
        <td><div class="b-row">
          <button class="ghost-btn" type="button" data-act="${m.loaded ? "unload" : "load"}">${m.loaded ? "Unload" : "Load"}</button>
          <button class="ghost-btn" type="button" data-act="bench">Benchmark</button>
          ${conf ? `<button class="ghost-btn danger" type="button" data-act="local-delete-confirm">Confirm remove</button><button class="ghost-btn" type="button" data-act="local-delete-cancel">Keep</button>`
                 : `<button class="ghost-btn danger" type="button" data-act="local-delete">Remove</button>`}</div></td></tr>`;
    }).join("");
    return `<section class="b-card" aria-labelledby="b-lm-h"><h3 id="b-lm-h">Local models</h3><p class="b-muted">${esc(hwLine)}</p>
      <div class="b-table-wrap"><table class="b-table"><thead><tr><th>Model</th><th>Size</th><th>State</th><th>Good for</th><th><span class="b-faint">Actions</span></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="b-faint">No models installed.</td></tr>'}</tbody></table></div>
      <p class="b-faint">Nothing is downloaded from here. Benchmarks run only against this computer.</p></section>`;
  }

  // ------------------------------------------------------------------ usage
  async function usage() {
    const [u, c] = await Promise.all([call("/usage"), call("/context")]);
    const models = Object.entries(u.models);
    const max = Math.max(1, ...models.map(([, v]) => v.requests));
    const table = models.length ? `<div class="b-table-wrap"><table class="b-table"><thead><tr><th>Model</th><th>Requests</th><th>Tokens in/out</th><th>p50 ms</th><th>Est. cost</th></tr></thead><tbody>
      ${models.map(([k, v]) => `<tr><td class="b-mono">${esc(k)}<div class="b-bar"><span style="width:${(v.requests / max) * 100}%"></span></div></td>
        <td>${esc(v.ok)}/${esc(v.requests)}</td><td>${esc(v.tokens_in)} / ${esc(v.tokens_out)}</td><td>${esc(v.p50_ms ?? "—")}</td><td>$${esc(v.cost_usd)}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="b-empty">No model calls recorded yet.</div>`;
    const fails = Object.entries(u.failures).map(([k, v]) => `<span class="b-tag warn">${esc(k)} × ${esc(v)}</span>`).join(" ") || '<span class="b-faint">none</span>';
    const rep = c.report;
    const ctx = rep ? `<div class="b-table-wrap"><table class="b-table"><thead><tr><th>Context category</th><th>Before</th><th>After</th></tr></thead><tbody>
      ${Object.keys({ ...rep.before, ...rep.after }).map((k) => `<tr><td>${esc(k)}</td><td>${esc(rep.before[k] || 0)}</td><td>${esc(rep.after[k] || 0)}</td></tr>`).join("")}
      <tr><td><strong>total</strong></td><td>${esc(rep.total_before)}</td><td>${esc(rep.total_after)} <span class="b-faint">(budget ${esc(rep.budget)})</span></td></tr></tbody></table></div>
      <p class="b-faint">Last ${esc(c.route)} request · ${esc(rep.dropped)} segments dropped · ${esc(rep.duplicates_removed)} duplicates · token counts only, no text.</p>`
      : `<div class="b-empty">No context assembled yet.</div>`;
    return `<section class="b-card"><h3>Usage by model</h3>${table}
        <p class="b-faint">Fallback rate ${(u.fallback_rate * 100).toFixed(0)}% over ${esc(u.events)} requests. Prompts and replies are never recorded.</p>
        <h4>Failures</h4><div class="b-row">${fails}</div></section>
      <section class="b-card"><h3>Context budget</h3>${ctx}</section>`;
  }

  // ------------------------------------------------------------------ rendering
  const RENDER = { overview, providers, routing, local, usage };
  async function render() {
    if (!body) return;
    for (const b of root.querySelectorAll(".brain-nav button")) b.setAttribute("aria-selected", String(b.dataset.view === st.view));
    try {
      body.innerHTML = await RENDER[st.view]();
    } catch (e) {
      body.innerHTML = `<div class="b-empty">The brain API isn't answering (${esc(e.message)}).</div>`;
    }
  }

  async function act(fn, done) {
    try { const out = await fn(); if (done) say(typeof done === "function" ? done(out) : done); await render(); }
    catch (e) { say(e.message, true); }
  }

  function wire() {
    root.querySelector(".brain-nav").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-view]");
      if (b) { st.view = b.dataset.view; say(""); render(); }
    });
    root.querySelector(".brain-nav").addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const i = VIEWS.indexOf(st.view) + (e.key === "ArrowRight" ? 1 : -1);
      st.view = VIEWS[(i + VIEWS.length) % VIEWS.length];
      render().then(() => root.querySelector(`.brain-nav [data-view="${st.view}"]`).focus());
      e.preventDefault();
    });
    body.addEventListener("click", (e) => {
      const el = e.target.closest("[data-act],[data-go]");
      if (!el) return;
      if (el.dataset.go) { st.view = el.dataset.go; render(); return; }
      const a = el.dataset.act;
      const card = el.closest("[data-pid]");
      const pid = card && card.dataset.pid;
      const kid = el.closest("[data-kid]")?.dataset.kid;
      const model = el.closest("[data-model]")?.dataset.model;
      const P = pid && encodeURIComponent(pid), K = kid && encodeURIComponent(kid), M = model && encodeURIComponent(model);
      const keys = () => (st.providers.find((p) => p.id === pid)?.keys || []).map((k) => k.id);
      const move = (d) => { const ids = keys(); const i = ids.indexOf(kid); const j = i + d; if (j < 0 || j >= ids.length) return null; [ids[i], ids[j]] = [ids[j], ids[i]]; return ids; };
      const handlers = {
        "key-add": () => { st.adding[pid] = true; render().then(() => body.querySelector(`[data-pid="${pid}"] input[name=secret]`)?.focus()); },
        "key-add-cancel": () => { st.adding[pid] = false; render(); },
        "key-up": () => { const ids = move(-1); if (ids) act(() => call(`/providers/${P}/keys/order`, { method: "POST", body: { ids } }), "Order saved."); },
        "key-down": () => { const ids = move(1); if (ids) act(() => call(`/providers/${P}/keys/order`, { method: "POST", body: { ids } }), "Order saved."); },
        "key-test": () => act(() => call(`/providers/${P}/keys/${K}/test`, { method: "POST" }), (r) => r.ok ? `Key works — ${r.models} models visible.` : `Key test failed: ${r.kind}${r.detail ? " — " + r.detail : ""}`),
        "key-toggle": () => { const k = st.providers.find((p) => p.id === pid).keys.find((x) => x.id === kid); act(() => call(`/providers/${P}/keys/${K}`, { method: "PATCH", body: { enabled: !k.enabled } }), "Saved."); },
        "key-rename": () => {
          // Edited in place: no dialog, which would block the overlay's window.
          const span = el.closest(".b-key").querySelector(".grow > span");
          span.contentEditable = "true";
          span.setAttribute("aria-label", "New key label, Enter to save");
          span.focus();
          span.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); span.blur(); } });
          span.addEventListener("blur", () => act(() => call(`/providers/${P}/keys/${K}`, { method: "PATCH", body: { label: span.textContent.trim() } }), "Renamed."), { once: true });
        },
        "key-migrate": () => act(() => call(`/providers/${P}/keys/migrate`, { method: "POST" }), "Copied into the keyring. Your .env file was not changed."),
        "key-delete": async () => { try { const r = await call(`/providers/${P}/keys/${K}/delete-request`, { method: "POST" }); st.confirm[kid] = r.confirm_token; say("Removing a key can't be undone. Confirm or keep it."); render(); } catch (err) { say(err.message, true); } },
        "key-delete-cancel": () => { delete st.confirm[kid]; say(""); render(); },
        "key-delete-confirm": () => { const t = st.confirm[kid]; delete st.confirm[kid]; act(() => call(`/providers/${P}/keys/${K}?token=${encodeURIComponent(t)}`, { method: "DELETE" }), "Key removed."); },
        "test": () => act(() => call(`/providers/${P}/test`, { method: "POST" }), (r) => r.ok ? `Connected — ${r.models} models.${r.missing_configured?.length ? " No longer offered: " + r.missing_configured.join(", ") : ""}` : `Connection failed: ${r.kind}${r.detail ? " — " + r.detail : ""}`),
        "model-add": () => { const v = card.querySelector("[data-field=model]").value.trim(); if (v) act(() => call(`/providers/${P}/models`, { method: "POST", body: { model_id: v } }), "Model added."); },
        "base": () => act(() => call(`/providers/${P}`, { method: "PATCH", body: { base_url: card.querySelector("[data-field=base]").value.trim() } }), "Address saved."),
        "validate": () => { say("Validating — a few small test requests…"); act(() => call("/models/validate", { method: "POST", body: { ref: el.dataset.ref } }), (r) => r.error ? r.error : Object.entries(r).map(([c, v]) => `${c}: ${v.ok ? "passed" : "failed"} (${v.score})`).join(" · ")); },
        "replace": () => act(() => call("/models/replace", { method: "POST", body: { ref: el.dataset.ref, new_id: el.dataset.new } }), "Replacement saved."),
        "load": () => act(() => call(`/local/${M}/load`, { method: "POST" }), "Loaded."),
        "unload": () => act(() => call(`/local/${M}/unload`, { method: "POST" }), "Unloaded."),
        "bench": () => { say("Benchmarking locally — this can take a minute…"); act(() => call(`/local/${M}/benchmark`, { method: "POST" }), (r) => `Passed ${r.passed.length} of ${Object.keys(r.roles).length} checks.`); },
        "local-delete": async () => { try { const r = await call(`/local/${M}/delete-request`, { method: "POST" }); st.confirm["local:" + model] = r.confirm_token; say("Removing deletes the model files. Confirm or keep it."); render(); } catch (err) { say(err.message, true); } },
        "local-delete-cancel": () => { delete st.confirm["local:" + model]; render(); },
        "local-delete-confirm": () => { const t = st.confirm["local:" + model]; delete st.confirm["local:" + model]; act(() => call(`/local/${M}?token=${encodeURIComponent(t)}`, { method: "DELETE" }), "Model removed."); },
      };
      if (handlers[a]) handlers[a]();
    });
    body.addEventListener("change", (e) => {
      const el = e.target;
      if (el.dataset.act === "toggle") {
        const pid = el.closest("[data-pid]").dataset.pid;
        act(() => call(`/providers/${encodeURIComponent(pid)}`, { method: "PATCH", body: { enabled: el.checked } }), el.checked ? "Provider enabled." : "Provider disabled.");
      }
      if (el.name === "profile") act(() => call("/routing", { method: "PUT", body: { profile: el.value } }), `Profile: ${el.value}.`);
    });
    body.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = e.target;
      if (f.dataset.act === "key-save") {
        const pid = f.closest("[data-pid]").dataset.pid;
        const secret = f.secret.value, label = f.label.value;
        f.secret.value = "";                     // the page never keeps it
        act(() => call(`/providers/${encodeURIComponent(pid)}/keys`, { method: "POST", body: { secret, label } }),
            () => { st.adding[pid] = false; return "Key saved to the keyring. Use Test to check it."; });
      }
      if (f.dataset.act === "provider-add") {
        const body_ = { template: f.template.value };
        if (f.id.value.trim()) body_.id = f.id.value.trim();
        if (f.base_url.value.trim()) body_.base_url = f.base_url.value.trim();
        act(() => call("/providers", { method: "POST", body: body_ }), "Provider added — now add its key.");
      }
      if (f.dataset.act === "routing-save") {
        const r = st.routing;
        const overrides = { ...r.overrides, [r.profile]: {} };
        for (const route of r.routes) {
          const picks = [...f.querySelectorAll(`select[data-route="${route}"]`)].map((s) => s.value).filter(Boolean);
          if (picks.length) overrides[r.profile][route] = picks;
        }
        act(() => call("/routing", { method: "PUT", body: {
          overrides,
          limits: { max_cost_usd: Number(f.max_cost_usd.value), max_latency_s: Number(f.max_latency_s.value),
                    cloud_escalation: f.cloud_escalation.checked, external_vision: f.external_vision.checked },
          privacy: { mode: f.mode.value, allow_screenshots: f.allow_screenshots.checked },
        } }), "Routing saved.");
      }
    });
  }

  function mount() {
    root = document.getElementById("brainRoot");
    if (!root) return;
    root.innerHTML = `<nav class="brain-nav" role="tablist" aria-label="Brain sections">
        ${VIEWS.map((v) => `<button type="button" role="tab" data-view="${v}" aria-selected="${v === st.view}">${v[0].toUpperCase() + v.slice(1)}</button>`).join("")}
      </nav>
      <p class="brain-status" role="status" aria-live="polite"></p>
      <div class="brain-body" tabindex="0" aria-label="Brain settings"></div>`;
    body = root.querySelector(".brain-body");
    status = root.querySelector(".brain-status");
    wire();
  }

  window.JarvisBrain = { refresh: () => { if (!root) mount(); return render(); }, show: (v) => { st.view = v; return render(); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
