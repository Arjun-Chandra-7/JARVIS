"""Rendered check of the integrated overlay: Brain, Study and 3D tabs, approvals, keyboard, themes.

Run by hand (needs Playwright's Chromium):  python tests/integration/overlay_check.py [screenshot_dir]

Serves the real backend routes (jarvis.webserver's app with its lifespan off: no agent, no
WhatsApp poller, no away mode) and the overlay files from one origin on port 18772, never the
live 8770. Temporary state, memory keys, fictional providers (brain_fakes), a stand-in 3D job.
Prints one line per check; exits non-zero if any fails.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests" / "brain")]
TMP = Path(tempfile.mkdtemp(prefix="overlay-ui-"))
os.environ.update(JARVIS_STATE_DIR=str(TMP / "state"), JARVIS_CONFIG_DIR=str(TMP / "config"),
                  JARVIS_BRAIN_CONFIG=str(TMP / "config" / "brain.json"), JARVIS_KEY_BACKEND="memory",
                  JARVIS_STUDY_DIR=str(TMP / "study"), JARVIS_DAILY_BRAIN="1",
                  JARVIS_RUNTIME_DIR=str(TMP / "run"), JARVIS_SETTINGS_EMIT="0",
                  GROQ_API_KEY="fictional-groq-env-key-0002")
os.environ.pop("GEMINI_API_KEY", None)
(TMP / "run").mkdir(parents=True, exist_ok=True)
PORT = 18772
FAKE_KEY = "fictional-overlay-key-4455667788"
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else TMP / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from brain_fakes import FakeProviders, fake_config  # noqa: E402
from jarvis import mobile, screen_safety, study_live, webserver  # noqa: E402
from jarvis.approvals import MANAGER  # noqa: E402
from jarvis.brain import adapters, daily  # noqa: E402
from jarvis.three_d import studio as studio_mod  # noqa: E402

fake = FakeProviders()
fake.models["api.groq.com"] = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
adapters.TRANSPORT["transport"] = fake.transport()
daily._BRAIN["b"] = daily.DailyBrain(fake_config(gemini=False))


class _Overlay:                      # the teach bus, recorded
    posts: list = []

    def work_area(self, monitor="primary"):
        return {"x": 0, "y": 0, "w": 1920, "h": 1080}

    def new_generation(self, lesson):
        return 1

    def send(self, cmds, at=None, gen=None):
        self.posts.append(cmds)
        return True

    def control(self, action, **extra):
        self.posts.append(action)
        return True


study_live.use(renderer=study_live.OverlayRenderer(overlay=_Overlay(), safety=lambda: screen_safety.Verdict(True)))
mobile._TRUSTED_ORIGINS |= {f"http://127.0.0.1:{PORT}"}

app = FastAPI()
app.middleware("http")(mobile.authorize)
for route in webserver.app.router.routes:
    if not isinstance(route, Mount):
        app.router.routes.append(route)
app.mount("/ui", StaticFiles(directory=REPO / "overlay", html=True), name="overlay")
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="off"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)

STUB = f"""
window.JARVIS_PORT = {PORT};
const noop = () => {{}};
window.jarvis = new Proxy({{}}, {{ get: (_t, k) => k === 'getState' ? (() => Promise.resolve({{form: 'workspace'}})) : noop }});
"""
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{' — ' + detail if detail else ''}", flush=True)


from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    exe = os.environ.get("BRAIN_UI_CHROME")
    browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    page = browser.new_page(viewport={"width": 900, "height": 900})
    page.add_init_script(STUB)
    console, bodies, posts = [], [], []
    page.on("console", lambda m: console.append((m.type, m.text, m.location.get("url", ""))))
    page.on("pageerror", lambda e: console.append(("pageerror", str(e), "")))

    def seen(resp):
        try:
            if any(p in resp.url for p in ("/brain/", "/study/", "/3d/", "/approvals")):
                bodies.append(resp.text())
        except Exception:  # noqa: BLE001
            pass
    page.on("response", seen)
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/approvals/" in r.url else None)
    page.goto(f"http://127.0.0.1:{PORT}/ui/index.html")
    page.evaluate("document.body.dataset.form = 'workspace'")

    # ---------------------------------------------------------------- structure
    ids = page.evaluate("() => { const c = {}; document.querySelectorAll('[id]').forEach(e => c[e.id] = (c[e.id]||0)+1);"
                        " return Object.entries(c).filter(([k,v]) => v > 1).map(([k]) => k); }")
    check("no duplicate element ids", not ids, ", ".join(ids))
    tabs = page.evaluate("[...document.querySelectorAll('#tabs [role=tab]')].map(t => t.id)")
    check("seven tabs, Brain/Study/3D present", tabs == ["tab-chat", "tab-tasks", "tab-memory", "tab-system",
                                                          "tab-brain", "tab-study", "tab-studio"], str(tabs))
    notes = page.evaluate("""() => { const out = [];
        const hint = document.querySelector('.keyhint').textContent; out.push(hint);
        return out; }""")
    check("key hint names Alt+1–7", "Alt+1–7" in notes[0] or "Alt+1–7" in notes[0], notes[0])
    alt_seen = {}
    for n, tab in enumerate(tabs, 1):
        page.keyboard.press(f"Alt+{n}")
        page.wait_for_timeout(120)
        alt_seen[n] = page.evaluate("document.querySelector('#tabs .tab.is-active').id")
    check("Alt+1..7 each select a different tab", len(set(alt_seen.values())) == 7 and
          [alt_seen[i] for i in range(1, 8)] == tabs, json.dumps(alt_seen))
    palette = page.evaluate("""() => { togglePalette(); const t = document.getElementById('paletteList').innerText;
        togglePalette(); return t; }""")
    shortcuts = [ln.strip() for ln in palette.splitlines() if ln.strip().startswith("Alt+")]
    check("palette shortcuts unique", len(shortcuts) == len(set(shortcuts)) and len(shortcuts) >= 7, str(shortcuts))

    # ---------------------------------------------------------------- Brain still loads
    page.click("#tab-brain")
    page.wait_for_selector(".b-routes", timeout=8000)
    check("Brain tab renders", page.locator(".b-route").count() == 5)
    page.click(".brain-nav [data-view=providers]")
    page.wait_for_selector("[data-pid=groq]")
    page.click("[data-pid=groq] [data-act=key-add]")
    page.fill("[data-pid=groq] input[name=secret]", FAKE_KEY)
    page.fill("[data-pid=groq] input[name=label]", "overlay check key")
    page.click("[data-pid=groq] form[data-act=key-save] button[type=submit]")
    page.wait_for_selector("text=Key saved to the keyring")
    leaked = page.evaluate(f"document.documentElement.outerHTML.includes('{FAKE_KEY}') || "
                           f"[...document.querySelectorAll('input')].some(i => i.value.includes('{FAKE_KEY}'))")
    check("fake provider key never in page state", not leaked and not any(FAKE_KEY in b for b in bodies))

    # ---------------------------------------------------------------- Study
    page.click("#tab-study")
    page.wait_for_selector("#studySession >> text=No study session")
    page.fill("#studyInput", "start a science study session")
    page.click("#studyForm button[type=submit]")
    page.wait_for_selector("#studySession >> text=running")
    check("study session view", "science" in page.locator("#studySession").inner_text().lower())
    page.fill("#studyInput", "quiz me on electricity")
    page.click("#studyForm button[type=submit]")
    page.wait_for_selector("#studyQuiz >> text=Question 1")
    quiz = page.locator("#studyQuiz").inner_text()
    check("quiz view shows the question, not the answer", "12" in quiz and "Not quite" not in quiz
          and "In series resistances add" not in quiz, quiz[:120])
    page.fill("#studyInput", "c")
    page.click("#studyForm button[type=submit]")
    page.wait_for_selector("#studyProgress >> text=Practice so far")
    check("progress view labelled as an indicator", "not a formal assessment" in
          page.locator("#studyProgress").inner_text().lower())
    page.fill("#studyInput", "explain refraction with a diagram")
    page.click("#studyForm button[type=submit]")
    page.wait_for_selector("#studySession >> text=diagram on screen")
    check("diagram request reached the teaching overlay", any(isinstance(p, list) for p in _Overlay.posts))

    # ---------------------------------------------------------------- approvals
    ran = []
    MANAGER.propose("reminder", "add a study reminder “Revise: current” for tomorrow at 6 pm",
                    {"title": "Revise"}, lambda: ran.append(1) or {"ok": True, "message": "Added."})
    MANAGER.propose("upload", "send the reference image to Remote 3D endpoint (mesh.example.test)",
                    {"app": "Remote", "to": "mesh.example.test"}, lambda: ran.append("upload"),
                    required=("mesh",), phrase="yes, send it to mesh.example.test")
    page.evaluate("JarvisStudy.refresh()")
    page.evaluate("JarvisStudy.refresh()")                  # re-rendered twice: no doubled handlers
    page.wait_for_selector("#studyApprovals >> text=Waiting for your yes")
    strip = page.locator("#studyApprovals").inner_text()
    check("pending approvals listed", "study reminder" in strip and "mesh.example.test" in strip)
    check("named approval has no button, only the phrase",
          page.locator("#studyApprovals [data-approve]").count() == 1 and "yes, send it to mesh.example.test" in strip)
    page.click("#studyApprovals [data-approve]")
    page.wait_for_timeout(500)
    check("approve runs exactly once", ran == [1] and len(posts) == 1, f"ran={ran} posts={len(posts)}")
    forced = page.evaluate(f"""async () => {{ const a = (await (await fetch('/approvals')).json()).pending[0];
        const r = await fetch('/approvals/' + a.id, {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{decision: 'confirm', fingerprint: a.fingerprint}})}});
        return (await r.json()).status; }}""")
    check("API refuses a button confirm for a named approval", forced == "needs_phrase" and "upload" not in ran, forced)
    MANAGER.clear()

    # ---------------------------------------------------------------- 3D
    job = SimpleNamespace(state=SimpleNamespace(value="building"), mode=SimpleNamespace(value="vector"),
                          fidelity="visually_matched", stages_done=["captured", "classified"], question="")
    studio_mod._STUDIO = SimpleNamespace(job=job, project=SimpleNamespace(current=2),
                                         recently_used=lambda s=600: True, status=lambda: "Building parts.")
    page.click("#tab-studio")
    page.wait_for_selector("#studioJob >> text=building")
    check("3D job progress view", "vector" in page.locator("#studioJob").inner_text()
          and "version 2" in page.locator("#studioJob").inner_text())
    page.click("#tab-chat")
    page.focus("#input") if page.locator("#input").count() else None
    before = page.evaluate("document.activeElement && document.activeElement.id")
    t0 = time.time()
    for i in range(20):
        page.evaluate(f"handleEvent('studio', 'Refining step {i}')")
    responsive = page.evaluate("1 + 1") == 2 and time.time() - t0 < 3
    after = page.evaluate("document.activeElement && document.activeElement.id")
    check("3D progress events do not block or steal focus", responsive and before == after, f"{before} → {after}")
    studio_mod._STUDIO = None

    # ---------------------------------------------------------------- notifications stay usable
    page.click("#tab-study")
    page.evaluate("toast('Notification: synthetic test')")
    page.wait_for_timeout(200)
    top = page.evaluate("""() => { const t = document.getElementById('toast'); const s = getComputedStyle(t);
        const r = t.getBoundingClientRect(); const z = Number(s.zIndex) || 0;
        const above = [...document.querySelectorAll('#view-study *, #view-studio *')]
          .filter(e => { const c = getComputedStyle(e); return c.position !== 'static' && (Number(c.zIndex) || 0) >= z; });
        return {shown: t.classList.contains('show'), inView: r.bottom <= innerHeight && r.top >= 0, z, above: above.length}; }""")
    check("study view never stacks above notification toasts", top["shown"] and top["inView"] and top["above"] == 0,
          json.dumps(top))

    # ---------------------------------------------------------------- accessibility
    unnamed = page.evaluate("""() => [...document.querySelectorAll('#view-study button, #view-study input, #view-studio button')]
      .filter(el => !(el.getAttribute('aria-label') || el.textContent.trim() || el.closest('label') ||
                      (el.id && document.querySelector(`label[for="${el.id}"]`)) || el.placeholder))
      .map(el => el.outerHTML.slice(0, 80))""")
    check("every Study/3D control has an accessible name", not unnamed, "; ".join(unnamed[:3]))
    labelled = page.evaluate("['view-study','view-studio'].every(id => document.getElementById(id).getAttribute('aria-labelledby'))")
    check("views are labelled tab panels", labelled)
    page.click("#tab-brain")
    page.focus("#tab-brain")
    page.keyboard.press("ArrowRight")
    check("arrow keys move focus along the tabs", page.evaluate("document.activeElement.id") == "tab-study")

    # ---------------------------------------------------------------- themes and widths
    colours = {}
    for appearance in ("dark", "light"):
        page.evaluate(f"document.body.dataset.appearance = '{appearance}'")
        page.wait_for_timeout(150)
        colours[appearance] = page.evaluate("getComputedStyle(document.querySelector('#studySession')).backgroundColor")
        page.screenshot(path=str(SHOTS / f"study-{appearance}.png"))
    check("Study follows dark and light tokens", colours["dark"] != colours["light"], json.dumps(colours))
    for w, h in ((360, 780), (1280, 800), (1920, 1080)):
        page.set_viewport_size({"width": w, "height": h})
        for tab in ("study", "studio", "brain"):
            page.click(f"#tab-{tab}")
            page.wait_for_timeout(120)
        over = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        reach = page.evaluate("""() => { const t = document.getElementById('tab-studio'); t.scrollIntoView({inline:'nearest'});
            const r = t.getBoundingClientRect(); return r.right <= window.innerWidth + 1 && r.left >= -1; }""")
        check(f"{w}px: no page overflow, every tab reachable", over[0] <= over[1] + 1 and reach, str(over))
        page.screenshot(path=str(SHOTS / f"study-{w}.png"))

    errors = [c for c in console if c[0] in ("error", "pageerror")]
    real = [c for c in errors if "/events" not in c[1] + c[2] and "net::ERR" not in c[1]]
    check("no console errors", not real, "; ".join(c[1][:90] for c in real[:4]))
    browser.close()

server.should_exit = True
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed · screenshots in {SHOTS}")
sys.exit(1 if failed else 0)
