#!/usr/bin/env python3
"""Exercise every Jarvis subsystem and report what actually works.

`--check` answers "are the dependencies installed". This answers the harder question: does each
feature still do its job on this machine, right now. It calls the real endpoints, dispatches the
real read-only tools, opens the real models, and reports one line per capability.

Nothing here sends a message, writes to a calendar, moves the mouse or spends money. Tools with
side effects are listed as `skipped (side effects)` rather than silently passed, so the report
never overstates what was verified.

    python scripts/audit.py                 # everything, against a running --web on 8770
    python scripts/audit.py --port 8791     # a backend on another port
    python scripts/audit.py --only memory   # one section
    python scripts/audit.py --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

_MARK = {OK: "\033[32m✓\033[0m", WARN: "\033[33m!\033[0m", FAIL: "\033[31m✗\033[0m", SKIP: "\033[90m·\033[0m"}

results: list[dict] = []


def record(section: str, name: str, status: str, detail: str = "") -> None:
    results.append({"section": section, "name": name, "status": status, "detail": detail})
    print(f"  {_MARK[status]} {name:<34} {detail[:96]}")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


def probe(sect: str, name: str, fn, *, warn_on_empty: bool = False):
    """Run a probe, turning an exception into a fail rather than ending the audit."""
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001 - the point is to report failures, not raise them
        record(sect, name, FAIL, f"{type(exc).__name__}: {exc}")
        return None
    if out is None or out == "" or out == []:
        record(sect, name, WARN if warn_on_empty else OK, "empty result")
        return out
    record(sect, name, OK, str(out).replace("\n", " ")[:96])
    return out


# --------------------------------------------------------------------------- endpoints
def audit_http(port: str) -> None:
    import httpx

    section(f"HTTP API (127.0.0.1:{port})")
    base = f"http://127.0.0.1:{port}"
    try:
        httpx.get(f"{base}/health", timeout=3)
    except Exception:  # noqa: BLE001
        record("http", "backend", FAIL, f"nothing listening on {port} — start: python -m jarvis --web")
        return

    endpoints = [
        ("GET", "/health", None), ("GET", "/stats", None), ("GET", "/weather", None),
        ("GET", "/suggestions", None), ("GET", "/history?limit=3", None), ("GET", "/nearby", None),
        ("GET", "/radar", None), ("GET", "/notifications", None), ("GET", "/power", None),
        ("GET", "/coding/jobs", None), ("GET", "/meet/status", None), ("GET", "/spotify", None),
        ("GET", "/whatsapp/inbox", None),
    ]
    for method, path, body in endpoints:
        t0 = time.time()
        try:
            r = httpx.request(method, base + path, json=body, timeout=25)
            ms = int((time.time() - t0) * 1000)
            if r.status_code != 200:
                record("http", path, FAIL, f"HTTP {r.status_code}")
                continue
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                record("http", path, WARN, f"{ms}ms · error: {str(data['error'])[:60]}")
            else:
                record("http", path, OK, f"{ms}ms · {json.dumps(data)[:70]}")
        except Exception as exc:  # noqa: BLE001
            record("http", path, FAIL, f"{type(exc).__name__}: {exc}")

    # The two that matter most: a plain answer, and one that must call a tool.
    for label, msg, expect in [
        ("chat (plain)", "Say the single word: pong.", None),
        ("chat (tool use)", "What is my CPU usage right now? Use your tools.", "%"),
    ]:
        t0 = time.time()
        try:
            r = httpx.post(f"{base}/chat", json={"message": msg}, timeout=180)
            reply = (r.json() or {}).get("reply", "")
            ms = int((time.time() - t0) * 1000)
            if expect and expect not in reply:
                record("http", label, WARN, f"{ms}ms · no tool data in reply: {reply[:60]}")
            else:
                record("http", label, OK, f"{ms}ms · {reply[:70]}")
        except Exception as exc:  # noqa: BLE001
            record("http", label, FAIL, f"{type(exc).__name__}: {exc}")

    # Streaming: prove deltas arrive before the final event, not all at once at the end.
    try:
        first_delta = None
        final_at = None
        t0 = time.time()
        with httpx.stream("POST", f"{base}/chat/stream", json={"message": "Count: one two three."},
                          timeout=180) as r:
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                d = json.loads(line[5:])
                if d["kind"] == "delta" and first_delta is None:
                    first_delta = time.time() - t0
                elif d["kind"] == "final":
                    final_at = time.time() - t0
        if first_delta is None:
            record("http", "/chat/stream", WARN, "no deltas — brain may not support streaming")
        else:
            record("http", "/chat/stream", OK,
                   f"first token {first_delta:.2f}s, complete {final_at or 0:.2f}s")
    except Exception as exc:  # noqa: BLE001
        record("http", "/chat/stream", FAIL, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- brain + tools
# Which tools have side effects is declared on the tool itself (jarvis/tools/base.py) and read
# from the registry below — this used to be a hand-copied set here, and it had already drifted:
# two names that were no longer tools, and two tools listed nowhere at all.

# Read-only tools worth actually calling, with arguments that are safe anywhere.
SAFE_CALLS = {
    "system_stats": {},
    "recall": {"query": "jarvis"},
    "list_dir": {"path": str(REPO)},
    "read_file": {"path": str(REPO / "README.md")},
    "who_is_around": {},
    "wifi_scan": {},
    "bluetooth_scan": {},
    "whatsapp_inbox": {},
    "find_contact": {"name": "zzzz-nobody"},
    "contact_context": {"name": "zzzz-nobody"},
    "conversation_search": {"query": "zzzz"},
    "read_clipboard": {},
    "check_coding_tasks": {},
    "check_pa_status": {},
    "list_automations": {},
    "get_activity_recordings": {},
    "google_agenda": {"days": "1"},
    "google_email_check": {},
    "google_tasks_list": {},
    "web_search": {"query": "hello"},
}


def audit_tools() -> None:
    from jarvis.config import CONFIG
    from jarvis.jobs.runner import JobRunner
    from jarvis.agent.groq_tools import build_registry

    section("Agent tools")
    from jarvis.agent.groq_tools import side_effect_tools

    schemas, dispatch = build_registry(CONFIG, JobRunner(CONFIG), None)
    names = [s["function"]["name"] for s in schemas]
    side_effects = side_effect_tools()
    record("tools", "registry", OK, f"{len(names)} tools registered")

    missing_desc = [n for n, s in zip(names, schemas) if not s["function"].get("description")]
    record("tools", "descriptions", FAIL if missing_desc else OK,
           f"{len(missing_desc)} without a description" if missing_desc else "all present")

    async def call(name, args):
        return await dispatch(name, args)

    for name in names:
        if name in SAFE_CALLS:
            t0 = time.time()
            try:
                out = str(asyncio.run(call(name, SAFE_CALLS[name])))
            except Exception as exc:  # noqa: BLE001
                record("tools", name, FAIL, f"{type(exc).__name__}: {exc}")
                continue
            ms = int((time.time() - t0) * 1000)
            low = out.lower()
            bad = low.startswith("tool error") or low.startswith("error:")
            stale = "expired" in low or "not connected" in low or "isn't connected" in low
            record("tools", name, FAIL if bad else (WARN if stale else OK),
                   f"{ms}ms · {out.replace(chr(10), ' ')[:80]}")
        elif name in side_effects:
            record("tools", name, SKIP, "not called (side effects)")
        else:
            record("tools", name, SKIP, "no safe arguments defined")


# --------------------------------------------------------------------------- memory
def audit_memory() -> None:
    from jarvis.config import CONFIG
    from jarvis.memory import embeddings
    from jarvis.memory.search import _embedder, recall
    from jarvis.memory.store import get_store

    section("Memory")
    store = get_store(CONFIG.vault_path)
    record("memory", "store", OK, json.dumps(store.stats()))

    record("memory", "ollama reachable", OK if embeddings.available() else WARN,
           "yes" if embeddings.available() else "no — keyword-only recall")
    embed = _embedder()
    record("memory", "embeddings usable", OK if embed else WARN,
           "nomic-embed responding" if embed else "server up but no embedding model pulled")

    probe("memory", "keyword recall", lambda: recall("jarvis", CONFIG.vault_path, k=2)[:90])
    if embed:
        hits = store.search("what do I know", k=3, embed=embed)
        record("memory", "hybrid search", OK if hits else WARN,
               f"{len(hits)} hits, signals={hits[0]['matched'] if hits else '-'}")

    from jarvis.memory import consolidate as mc
    record("memory", "consolidation", OK,
           f"last run {'never' if not mc.last_run() else time.strftime('%Y-%m-%d %H:%M', time.localtime(mc.last_run()))}"
           f", due={mc.due()}")

    from jarvis.agent.tool_router import ToolRouter
    from jarvis.jobs.runner import JobRunner
    from jarvis.agent.groq_tools import build_registry
    schemas, _ = build_registry(CONFIG, JobRunner(CONFIG), None)
    router = ToolRouter(schemas)
    picked = router.explain("what is my cpu usage")
    record("memory", "tool router", OK if "system_stats" in picked else FAIL,
           f"{len(picked)} tools for a CPU question; system_stats "
           f"{'present' if 'system_stats' in picked else 'MISSING'}")


# --------------------------------------------------------------------------- voice
def audit_voice() -> None:
    from jarvis.config import CONFIG

    section("Voice")
    backend = CONFIG.resolved_voice_backend()
    record("voice", "backend", OK, backend)

    for mod in ("faster_whisper", "piper", "openwakeword", "sounddevice", "pvrecorder"):
        try:
            __import__(mod)
            record("voice", mod, OK, "importable")
        except Exception as exc:  # noqa: BLE001
            record("voice", mod, FAIL, str(exc)[:70])

    piper = Path(CONFIG.piper_model)
    record("voice", "piper voice", OK if piper.exists() else FAIL, str(piper))

    from jarvis.audio import neural_vad
    det = neural_vad.load(16000)
    if det is None:
        record("voice", "silero vad", WARN, neural_vad.describe())
    else:
        import numpy as np
        rng = np.random.default_rng(0)
        det.reset()
        noise = det.feed((rng.standard_normal(1536) * 900).astype(np.int16))
        record("voice", "silero vad", OK if noise < 0.5 else WARN,
               f"white noise scored {noise:.3f} (should be low)")

    try:
        import sounddevice as sd
        ins = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
        record("voice", "input devices", OK if ins else FAIL, f"{len(ins)}: {', '.join(ins[:2])}")
    except Exception as exc:  # noqa: BLE001
        record("voice", "input devices", FAIL, str(exc)[:70])


# --------------------------------------------------------------------------- integrations
def audit_integrations() -> None:
    from jarvis.config import CONFIG

    section("Integrations")

    # Ollama
    try:
        import httpx
        r = httpx.get(CONFIG.ollama_base.rsplit("/v1", 1)[0] + "/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        has = any(m.split(":")[0] == CONFIG.ollama_model.split(":")[0] for m in models)
        record("integrations", "ollama", OK if has else WARN,
               f"{len(models)} models: {', '.join(models[:4])}")
    except Exception as exc:  # noqa: BLE001
        record("integrations", "ollama", FAIL, str(exc)[:70])

    # Google
    try:
        from jarvis.integrations.google.auth import load_credentials, status
        ok = load_credentials(CONFIG) is not None
        record("integrations", "google", OK if ok else WARN, "connected" if ok else status(CONFIG)[:90])
    except Exception as exc:  # noqa: BLE001
        record("integrations", "google", FAIL, str(exc)[:70])

    # WhatsApp bridge
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:8765/status", timeout=2)
        record("integrations", "whatsapp bridge", OK, r.text[:70])
    except Exception:  # noqa: BLE001
        record("integrations", "whatsapp bridge", WARN, "not running (:8765)")

    # KDE Connect
    try:
        out = subprocess.run(["kdeconnect-cli", "-a", "--id-only"],
                             capture_output=True, text=True, timeout=6).stdout.strip()
        record("integrations", "kde connect", OK if out else WARN,
               out.replace("\n", ",")[:60] or "no paired device reachable")
    except FileNotFoundError:
        record("integrations", "kde connect", WARN, "kdeconnect-cli not installed")
    except Exception as exc:  # noqa: BLE001
        record("integrations", "kde connect", WARN, str(exc)[:60])

    # Browser-driven integrations: a logged-in profile on disk is the precondition.
    for label, path in [
        ("chatgpt profile", Path("~/.config/jarvis/chatgpt-profile").expanduser()),
        ("perplexity profile", Path("~/.config/jarvis/perplexity-profile").expanduser()),
    ]:
        ready = path.exists() and any(path.iterdir())
        record("integrations", label, OK if ready else WARN,
               "signed in" if ready else f"not signed in — {path.name.split('-')[0]} login needed")

    try:
        import playwright  # noqa: F401
        record("integrations", "playwright", OK, "installed")
    except ImportError:
        record("integrations", "playwright", WARN, "not installed — browser features disabled")

    # Screen capture (Wayland portal)
    try:
        from jarvis.vision import screenshot
        path = screenshot.capture()
        record("integrations", "screen capture", OK if path else WARN,
               str(path) if path else "returned nothing (portal may need approval)")
    except Exception as exc:  # noqa: BLE001
        record("integrations", "screen capture", WARN, f"{type(exc).__name__}: {exc}"[:70])


# --------------------------------------------------------------------------- frontend
def audit_frontend() -> None:
    section("Overlay")
    overlay = REPO / "overlay"
    for js in sorted(overlay.glob("*.js")):
        r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
        record("overlay", js.name, OK if r.returncode == 0 else FAIL,
               "syntax ok" if r.returncode == 0 else r.stderr.strip().splitlines()[0][:80])

    for f in ("index.html", "style.css", "reactor.js", "renderer.js"):
        p = overlay / f
        record("overlay", f, OK if p.exists() else FAIL, f"{p.stat().st_size} bytes" if p.exists() else "missing")

    node_modules = overlay / "node_modules"
    record("overlay", "node_modules", OK if node_modules.exists() else WARN,
           "present" if node_modules.exists() else "run: cd overlay && npm install")


SECTIONS = {
    "http": audit_http, "tools": audit_tools, "memory": audit_memory,
    "voice": audit_voice, "integrations": audit_integrations, "overlay": audit_frontend,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8770")
    ap.add_argument("--only", action="append", choices=sorted(SECTIONS), help="run one section")
    ap.add_argument("--json", dest="json_out", help="also write the raw results here")
    args = ap.parse_args()

    print("\033[1mJarvis feature audit\033[0m")
    for name in (args.only or list(SECTIONS)):
        fn = SECTIONS[name]
        fn(args.port) if name == "http" else fn()

    counts = {s: sum(1 for r in results if r["status"] == s) for s in (OK, WARN, FAIL, SKIP)}
    print(f"\n\033[1msummary\033[0m  {counts[OK]} ok · {counts[WARN]} warn · "
          f"{counts[FAIL]} fail · {counts[SKIP]} skipped")

    if counts[FAIL]:
        print("\nfailures:")
        for r in results:
            if r["status"] == FAIL:
                print(f"  - [{r['section']}] {r['name']}: {r['detail']}")
    if counts[WARN]:
        print("\nneeds attention:")
        for r in results:
            if r["status"] == WARN:
                print(f"  - [{r['section']}] {r['name']}: {r['detail']}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json_out}")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
