"""Browser check of the overlay's Brain tab against the real /brain API and a fake provider server.

Run by hand (needs Playwright's Chromium):  python tests/brain/ui_check.py [screenshot_dir]

Nothing real is touched: temporary settings/state dirs, the memory key backend, fictional keys,
every provider request answered by brain_fakes.FakeProviders, and its own port (18771), never
the live backend's 8770. Prints one line per check and exits non-zero if any fails.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO), str(HERE)]
TMP = Path(tempfile.mkdtemp(prefix="brain-ui-"))
os.environ.update(JARVIS_STATE_DIR=str(TMP / "state"), JARVIS_CONFIG_DIR=str(TMP / "config"),
                  JARVIS_BRAIN_CONFIG=str(TMP / "config" / "brain.json"), JARVIS_KEY_BACKEND="memory",
                  JARVIS_DAILY_BRAIN="1", GROQ_API_KEY="fictional-groq-env-key-0001")
os.environ.pop("GEMINI_API_KEY", None)
PORT = 18771
FAKE_KEY = "fictional-ui-key-5566778899"
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else TMP / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from brain_fakes import FakeProviders, fake_config  # noqa: E402
from jarvis import mobile  # noqa: E402
from jarvis.brain import adapters, daily, executor, telemetry  # noqa: E402

fake = FakeProviders()
fake.models["api.groq.com"] = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
fake.ollama_tags = [{"name": "qwen2.5:3b", "size": 1_930_000_000, "details": {"parameter_size": "3.1B"}}]
adapters.TRANSPORT["transport"] = fake.transport()
daily._BRAIN["b"] = daily.DailyBrain(fake_config(gemini=False))
mobile._TRUSTED_ORIGINS |= {f"http://127.0.0.1:{PORT}"}
for i in range(3):
    telemetry.record(request_id=f"r{i}", route="chat", intent="conversation", provider="groq",
                     model="openai/gpt-oss-20b", status="ok", latency_ms=400 + i, tokens_in=120, tokens_out=40)

app = FastAPI()
app.middleware("http")(mobile.authorize)
from jarvis.brain.api import router  # noqa: E402
app.include_router(router)
app.mount("/", StaticFiles(directory=REPO / "overlay", html=True), name="overlay")
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)

STUB = f"""
window.JARVIS_PORT = {PORT};
const noop = () => {{}};
window.jarvis = new Proxy({{}}, {{ get: (_t, k) => k === 'getState' ? (() => Promise.resolve({{form: 'workspace'}}))
                                          : (k.startsWith('on') ? noop : noop) }});
"""

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{' — ' + detail if detail else ''}", flush=True)


from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    exe = os.environ.get("BRAIN_UI_CHROME")      # an already-installed Chromium, if the bundled one is missing
    browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    page = browser.new_page(viewport={"width": 760, "height": 900})
    page.add_init_script(STUB)
    console, bodies = [], []
    page.on("console", lambda m: console.append((m.type, m.text, m.location.get("url", ""))))
    page.on("pageerror", lambda e: console.append(("pageerror", str(e), "")))

    def grab(resp):
        if "/brain/" in resp.url:
            try:
                bodies.append(resp.text())
            except Exception:  # noqa: BLE001
                pass
    page.on("response", grab)
    page.goto(f"http://127.0.0.1:{PORT}/index.html")
    page.evaluate("document.body.dataset.form = 'workspace'")
    page.click("#tab-brain")
    page.wait_for_selector(".b-routes", timeout=8000)
    check("overview renders route cards", page.locator(".b-route").count() == 5)
    page.screenshot(path=str(SHOTS / "overview.png"))

    # providers
    page.click(".brain-nav [data-view=providers]")
    page.wait_for_selector("[data-pid=groq]")
    cards = page.locator("section.b-card[data-pid]").count()
    check("provider cards", cards >= 2, f"{cards} cards")
    page.click("[data-pid=groq] [data-act=key-add]")
    field = page.locator("[data-pid=groq] input[name=secret]")
    check("key field is masked", field.get_attribute("type") == "password"
          and field.get_attribute("autocomplete") == "new-password")
    field.fill(FAKE_KEY)
    page.fill("[data-pid=groq] input[name=label]", "UI test key")
    page.click("[data-pid=groq] form[data-act=key-save] button[type=submit]")
    page.wait_for_selector("text=Key saved to the keyring")
    rows = page.locator("[data-pid=groq] .b-key")
    check("key added", rows.count() == 2, f"{rows.count()} keys listed")
    html = page.content()
    check("saved key never returned to the page", FAKE_KEY not in html and FAKE_KEY[-6:] not in html)
    check("saved key in no API response", not any(FAKE_KEY in b or FAKE_KEY[-6:] in b for b in bodies))
    in_state = page.evaluate(f"JSON.stringify(Object.keys(window)).includes('{FAKE_KEY}') || "
                             f"[...document.querySelectorAll('input')].some(i => i.value.includes('{FAKE_KEY}'))")
    check("no key left in inputs or globals", not in_state)

    first = rows.nth(0).get_attribute("data-kid")
    page.click(f"[data-kid='{first}'] [data-act=key-down]")
    page.wait_for_selector("text=Order saved")
    check("reorder keys", page.locator("[data-pid=groq] .b-key").nth(1).get_attribute("data-kid") == first)

    new_kid = [page.locator("[data-pid=groq] .b-key").nth(i).get_attribute("data-kid") for i in range(2)]
    new_kid = [k for k in new_kid if not k.startswith("env-")][0]
    page.click(f"[data-kid='{new_kid}'] [data-act=key-test]")
    page.wait_for_selector("text=Key works")
    check("test key connection", True, page.locator(".brain-status").inner_text())

    fake.set("models@api.groq.com", ("status", 401, {"error": {"message": "Invalid API Key"}}))
    page.click("[data-pid=groq] [data-act=test]")
    page.wait_for_selector("text=Connection failed")
    check("provider failure shown with safe reason", "auth_failed" in page.locator(".brain-status").inner_text())
    fake.behaviour.pop("models@api.groq.com")

    page.click(f"[data-kid='{new_kid}'] [data-act=key-delete]")
    page.wait_for_selector(f"[data-kid='{new_kid}'] [data-act=key-delete-confirm]")
    check("remove asks for confirmation", page.locator(f"[data-kid='{new_kid}']").count() == 1)
    page.click(f"[data-kid='{new_kid}'] [data-act=key-delete-confirm]")
    page.wait_for_selector("text=Key removed")
    check("remove key", page.locator(f"[data-kid='{new_kid}']").count() == 0)

    page.fill("[data-pid=groq] [data-field=model]", "llama3-70b-8192")
    page.click("[data-pid=groq] [data-act=model-add]")
    page.wait_for_selector("text=Model added")
    check("manual model id", "llama3-70b-8192" in page.locator("[data-pid=groq] .b-models").inner_text())
    page.screenshot(path=str(SHOTS / "providers-full.png"), full_page=True)

    # routing
    page.click(".brain-nav [data-view=routing]")
    page.wait_for_selector("form[data-act=routing-save]")
    page.check("input[name=profile][value=study]")
    page.wait_for_selector("text=Profile: study")
    page.select_option("select[data-route=chat][data-slot='0']", "groq/openai/gpt-oss-20b")
    page.select_option("select[name=mode]", "prefer_local")
    page.click("form[data-act=routing-save] button[type=submit]")
    page.wait_for_selector("text=Routing saved")
    saved = json.loads(Path(os.environ["JARVIS_BRAIN_CONFIG"]).read_text())
    check("routing profile and order saved", saved["profile"] == "study"
          and saved["routing"]["study"]["chat"] == ["groq/openai/gpt-oss-20b"]
          and saved["privacy"]["mode"] == "prefer_local")
    check("settings file holds no key", FAKE_KEY not in json.dumps(saved) and "fictional-groq-env" not in json.dumps(saved))

    # offline + provider failure states on the overview
    executor.mark_offline()
    page.click(".brain-nav [data-view=overview]")
    page.wait_for_selector(".b-routes")
    check("offline state shown", page.locator(".b-tag", has_text="offline").count() == 1)
    executor.mark_online()

    # local and usage
    page.click(".brain-nav [data-view=local]")
    page.wait_for_selector("tr[data-model]")
    page.screenshot(path=str(SHOTS / "local.png"))
    check("local models listed", page.locator("tr[data-model]").count() == 1, page.locator(".brain-body").inner_text()[:300])
    page.click(".brain-nav [data-view=usage]")
    page.wait_for_selector("text=Usage by model")
    check("usage view", "groq/openai/gpt-oss-20b" in page.locator(".brain-body").inner_text())

    # keyboard
    page.focus(".brain-nav [data-view=usage]")
    page.keyboard.press("ArrowLeft")
    page.wait_for_selector("text=Local models")
    check("keyboard navigation between sections",
          page.evaluate("document.activeElement.dataset.view") == "local")

    # accessible names
    page.click(".brain-nav [data-view=providers]")
    page.wait_for_selector("[data-pid=groq]")
    unnamed = page.evaluate("""() => [...document.querySelectorAll('#brainRoot button, #brainRoot input, #brainRoot select')]
      .filter(el => !(el.getAttribute('aria-label') || el.textContent.trim() || el.closest('label') ||
                      (el.id && document.querySelector(`label[for="${el.id}"]`)) || el.placeholder))
      .map(el => el.outerHTML.slice(0, 80))""")
    check("every control has an accessible name", not unnamed, "; ".join(unnamed[:3]))

    # themes
    colours = {}
    for appearance in ("dark", "light"):
        page.evaluate(f"document.body.dataset.appearance = '{appearance}'")
        page.wait_for_timeout(150)
        colours[appearance] = page.evaluate("""() => { const c = document.querySelector('.b-card');
            const s = getComputedStyle(c); return [s.backgroundColor, getComputedStyle(c.querySelector('h3')).color]; }""")
        page.screenshot(path=str(SHOTS / f"providers-{appearance}.png"))
    check("theme tokens followed in dark and light", colours["dark"] != colours["light"], json.dumps(colours))

    # narrow window
    page.set_viewport_size({"width": 360, "height": 800})
    page.wait_for_timeout(200)
    overflow = page.evaluate("""() => { const b = document.querySelector('.brain-body');
        return [b.scrollWidth, b.clientWidth]; }""")
    check("narrow window: no horizontal overflow", overflow[0] <= overflow[1] + 1, f"{overflow}")
    page.screenshot(path=str(SHOTS / "providers-narrow.png"), full_page=True)

    brain_errors = [c for c in console if c[0] in ("error", "pageerror") and ("brain" in c[1] + c[2] or c[0] == "pageerror")]
    other = [c for c in console if c[0] in ("error", "pageerror") and c not in brain_errors]
    check("no console errors from the Brain tab", not brain_errors, "; ".join(c[1][:90] for c in brain_errors[:3]))
    print(f"note: {len(other)} other console errors (overlay endpoints this harness does not serve): "
          + "; ".join(sorted({c[1][:70] for c in other})[:4]))
    browser.close()

server.should_exit = True
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed · screenshots in {SHOTS}")
sys.exit(1 if failed else 0)
