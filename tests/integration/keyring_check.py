"""Real-keyring check of the Brain's key management, with fictional keys only.

Run by hand:  python tests/integration/keyring_check.py

Uses the *system* Secret Service (gnome-keyring) through the Brain's own HTTP API, with temporary
settings/state, a fictional provider ``kr-test-<hex>`` and two fictional keys carrying the same
unique id. Real keys are never read, changed or listed (the check counts other Jarvis items before
and after, by attribute only). Everything it creates is deleted, also on failure. Prints one line
per check; exits non-zero on any failure.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import sys
import tempfile
import threading
import time

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix="keyring-check-"))
os.environ.update(JARVIS_STATE_DIR=str(TMP / "state"), JARVIS_CONFIG_DIR=str(TMP / "config"),
                  JARVIS_BRAIN_CONFIG=str(TMP / "config" / "brain.json"), JARVIS_DAILY_BRAIN="1")
for var in ("JARVIS_KEY_BACKEND", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(var, None)                              # this process only: .env is not touched
TAG = secrets.token_hex(4)
PID = f"kr-test-{TAG}"
KEYS = [f"fictional-keyring-test-{TAG}-A-DO-NOT-USE", f"fictional-keyring-test-{TAG}-B-DO-NOT-USE"]
PORT = 18773
ORIGIN = "http://127.0.0.1:8770"

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from jarvis import mobile  # noqa: E402
from jarvis.brain import daily  # noqa: E402
from jarvis.brain.api import router  # noqa: E402
from jarvis.brain.keys import SecretServiceBackend  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{' — ' + detail if detail else ''}", flush=True)


ss = SecretServiceBackend()
if not ss.available():
    print("BLOCKED  the Secret Service is not available on this session; no key was written.")
    sys.exit(2)


def jarvis_items() -> list[str]:
    """Paths of every Jarvis keyring item (attributes only; no secret is read)."""
    async def go():
        bus, service, _ = await ss._session()
        try:
            unlocked, locked = await service.call_search_items({"application": "jarvis"})
            return sorted(list(unlocked) + list(locked))
        finally:
            bus.disconnect()
    return ss._run(go)


before = jarvis_items()
daily._BRAIN["b"] = None
app = FastAPI()
app.middleware("http")(mobile.authorize)
app.include_router(router)
log = io.StringIO()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info"))


def _serve():
    import logging
    handler = logging.StreamHandler(log)                 # the server's own log, kept to be searched
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "jarvis"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
    server.run()


threading.Thread(target=_serve, daemon=True).start()
while not server.started:
    time.sleep(0.05)

api = httpx.Client(base_url=f"http://127.0.0.1:{PORT}/brain", headers={"Origin": ORIGIN}, timeout=20)
bodies: list[str] = []


def call(method: str, path: str, **kw):
    r = api.request(method, path, **kw)
    bodies.append(r.text)
    return r


kids: list[str] = []
try:
    r = call("POST", "/providers", json={"template": "custom", "id": PID, "base_url": "https://example.invalid/v1",
                                          "display_name": f"Keyring test {TAG}"})
    check("fictional provider added (temporary settings)", r.status_code == 200, str(r.status_code))
    for i, key in enumerate(KEYS):
        r = call("POST", f"/providers/{PID}/keys", json={"secret": key, "label": f"test {TAG} {i}"})
        ok = r.status_code == 200
        if ok:
            kids.append(r.json()["key"]["id"])
        check(f"add key {i + 1}", ok, r.text[:80] if not ok else "")
    stored = [ss.get(k) for k in kids]
    check("stored in the system keyring", stored == KEYS)
    view = call("GET", "/providers").json()
    prov = next(p for p in view["providers"] if p["id"] == PID)
    rows = {k["id"]: k for k in prov["keys"]}
    check("read returns a fingerprint only", all(len(rows[k]["fingerprint"]) == 6 for k in kids)
          and all(set(rows[k]) >= {"id", "label", "fingerprint", "storage"} for k in kids),
          ", ".join(rows[k]["storage"] for k in kids))
    r = call("PATCH", f"/providers/{PID}/keys/{kids[0]}", json={"enabled": False})
    after = {k["id"]: k for k in r.json()["keys"]}
    check("disable", after[kids[0]]["enabled"] is False)
    r = call("POST", f"/providers/{PID}/keys/order", json={"ids": [kids[1], kids[0]]})
    order = [k["id"] for k in r.json()["keys"]]
    check("reorder", order[:2] == [kids[1], kids[0]], str(order))
    r = call("DELETE", f"/providers/{PID}/keys/{kids[0]}", params={"token": "guess"})
    check("delete without confirmation is refused", r.status_code == 400 and ss.get(kids[0]) == KEYS[0])
    for kid in list(kids):
        token = call("POST", f"/providers/{PID}/keys/{kid}/delete-request").json()["confirm_token"]
        r = call("DELETE", f"/providers/{PID}/keys/{kid}", params={"token": token})
        check(f"delete {kid} with its confirmation", r.status_code == 200)
    check("deletion confirmed in the keyring", all(ss.get(k) is None for k in kids))
    check("other Jarvis keyring items unchanged", jarvis_items() == before, f"{len(before)} before")
    everything = "\n".join(bodies)
    events = "".join(p.read_text() for p in (TMP / "state").glob("*.jsonl"))
    settings = (TMP / "config" / "brain.json").read_text() if (TMP / "config" / "brain.json").exists() else ""
    for label, text in (("API output", everything), ("server log", log.getvalue()), ("event logs", events),
                        ("settings file", settings)):
        check(f"full key never in {label}", not any(k in text or k[-12:] in text for k in KEYS))
finally:
    for kid in kids:
        try:
            ss.delete(kid)
        except Exception:  # noqa: BLE001
            pass
    left = [k for k in kids if ss.get(k) is not None]
    print(f"cleanup: {len(kids)} test item(s) removed, {len(left)} left")
    server.should_exit = True

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed (test id {TAG})")
sys.exit(1 if failed else 0)
