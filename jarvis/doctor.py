"""`jarvis doctor` — is the installed Jarvis working, and what is degraded? Safe to run any time.

Read-only and local. It never sends a message, replies to a notification, sends mail, creates an
event, starts away mode, activates a repair, types into anything, records audio, or prints a
secret: cloud providers are judged from the backend's own cached health check (made at startup,
model lists only), keys by whether they are set — never their values.

Each check says one of: ready · degraded · unavailable · disabled · needs user action. A failed
provider is "degraded" when Jarvis has a working fallback; only the backend being down makes the
whole installation "unavailable".

    python -m jarvis.doctor            human summary
    python -m jarvis.doctor --json     machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

READY, DEGRADED, UNAVAILABLE, DISABLED, USER = "ready", "degraded", "unavailable", "disabled", "needs user action"
PORT = int(os.environ.get("JARVIS_WEB_PORT", "8770"))
WA_PORT = int(os.environ.get("WA_PORT", "8765"))
RUN = Path(os.environ.get("JARVIS_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
UNITS = ("jarvis-backend", "jarvis-voice", "jarvis-whatsapp")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    data: dict = field(default_factory=dict)
    core: bool = False


def _run(argv: list[str], timeout: float = 5) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _get(url: str, timeout: float = 3) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read())
            return data if isinstance(data, dict) else {"_list": data}
    except (OSError, ValueError):
        return None


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class _quiet_stderr:
    """Silence C-level stderr (native libraries print warnings there) for the block."""

    def __enter__(self):
        sys.stderr.flush()
        self.saved = os.dup(2)
        self.null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.null, 2)

    def __exit__(self, *exc):
        os.dup2(self.saved, 2)
        os.close(self.saved)
        os.close(self.null)
        return False


def _unit(unit: str) -> dict:
    props = _run(["systemctl", "--user", "show", f"{unit}.service", "-p",
                  "ActiveState,SubState,MainPID,NRestarts,LoadState,ActiveEnterTimestampMonotonic"])
    out = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
    return out


# ------------------------------------------------------------------------------ checks
def check_services(head: str) -> list[Check]:
    health = _get(f"http://127.0.0.1:{PORT}/health") or {}
    loaded = {"jarvis-backend": (health.get("build") or {}).get("commit"),
              "jarvis-voice": (health.get("voice_build") or {}).get("commit") or
              (_read_json(RUN / "jarvis-build-voice.json") or {}).get("commit"),
              "jarvis-whatsapp": (_get(f"http://127.0.0.1:{WA_PORT}/status") or {}).get("commit")}
    checks = []
    for unit in UNITS:
        u = _unit(unit)
        restarts = int(u.get("NRestarts", "0") or 0)
        commit = loaded.get(unit)
        data = {"active": u.get("ActiveState"), "pid": int(u.get("MainPID", "0") or 0), "restarts": restarts,
                "commit": commit}
        if u.get("LoadState") != "loaded":
            checks.append(Check(unit, DISABLED, "not installed", data))
            continue
        if u.get("ActiveState") != "active":
            status = UNAVAILABLE if unit != "jarvis-whatsapp" else DEGRADED
            checks.append(Check(unit, status, f"{u.get('ActiveState')}/{u.get('SubState')}", data,
                                core=unit != "jarvis-whatsapp"))
            continue
        if commit and head and commit[:12] != head[:12]:
            checks.append(Check(unit, DEGRADED, f"running {commit[:12]}, checkout is {head[:12]} — restart to load it",
                                data, core=unit != "jarvis-whatsapp"))
        elif restarts >= 3:
            checks.append(Check(unit, DEGRADED, f"restarted {restarts}× — crashing?", data,
                                core=unit != "jarvis-whatsapp"))
        else:
            checks.append(Check(unit, READY, f"active on {(commit or 'unknown')[:12]}", data,
                                core=unit != "jarvis-whatsapp"))
    return checks


def check_ports(services: list[Check]) -> Check:
    listening = _run(["ss", "-ltnpH"])
    pids = {c.name: c.data.get("pid") for c in services}
    rows = {}
    problems = []
    for port, unit in ((PORT, "jarvis-backend"), (WA_PORT, "jarvis-whatsapp")):
        line = next((ln for ln in listening.splitlines() if f":{port} " in ln), "")
        addr = line.split()[3] if len(line.split()) > 3 else ""
        owner_ok = f"pid={pids.get(unit)}," in line if pids.get(unit) else False
        rows[str(port)] = {"address": addr, "owner": unit if owner_ok else ("other" if line else None)}
        if not line:
            problems.append(f"nothing on {port}")
        elif not owner_ok:
            problems.append(f"{port} held by another process")
        if addr and not addr.startswith(("127.", "[::1]", "[::ffff:127.")):
            if port == WA_PORT:
                problems.append(f"the WhatsApp bridge listens on {addr}")
            else:
                rows[str(port)]["note"] = "reachable from the LAN; every non-loopback request needs the phone token"
    status = READY if not problems else (UNAVAILABLE if f"nothing on {PORT}" in problems else DEGRADED)
    return Check("ports", status, "; ".join(problems) or "8770 backend, 8765 WhatsApp (loopback)", rows)


def check_overlay(head: str = "") -> Check:
    running = bool(_run(["pgrep", "-f", "overlay/node_modules/electron/dist/electron"]).strip())
    report = _read_json(RUN / "jarvis-effective-overlay.json")
    build = _read_json(RUN / "jarvis-build-overlay.json") or {}
    if not running:
        return Check("overlay", DEGRADED, "not running — `jarvis start` opens it; voice still works")
    commit = str(build.get("commit") or "")
    if commit and head and commit[:12] != head[:12]:
        return Check("overlay", DEGRADED, f"running {commit[:12]}, checkout is {head[:12]} — restart it to load it",
                     {"commit": commit})
    if not report:
        return Check("overlay", DEGRADED, "running, but has not reported its settings (older code?)")
    return Check("overlay", READY, f"connected on {(commit or 'unknown')[:12]}; motion "
                                   f"{report.get('observed', {}).get('motion')}",
                 {"observed": report.get("observed", {}), "revision": report.get("revision"), "commit": commit})


def check_voice() -> Check:
    build = _read_json(RUN / "jarvis-build-voice.json")
    report = _read_json(RUN / "jarvis-effective-voice.json")
    if not build:
        return Check("voice", UNAVAILABLE, "the voice process has not started", core=True)
    try:
        os.kill(int(build.get("pid", 0)), 0)
    except (OSError, ValueError):
        return Check("voice", UNAVAILABLE, "the voice process has exited", core=True)
    if not report:
        return Check("voice", DEGRADED, "running; settings watcher has not reported", core=True)
    obs = report.get("observed", {})
    return Check("voice", READY, f"{obs.get('engine')} voice, speed {obs.get('neutral_speed')}, "
                                 f"follow-up {obs.get('window_s')} s", obs, core=True)


def check_microphone() -> Check:
    """Muted or not — read only. A muted microphone left the wake word unheard for a day and a
    half while every other check here said ready."""
    from .audio import inputs

    muted = inputs.is_muted()
    if muted is None:
        return Check("microphone", DEGRADED, "could not ask wireplumber whether it is muted")
    if muted:
        return Check("microphone", USER, "muted — Jarvis cannot hear the wake word; "
                                         "the mute key, Settings, or push-to-talk will lift it")
    return Check("microphone", READY, "not muted")


def check_stt() -> Check:
    try:
        import ctranslate2  # noqa: F401
        import faster_whisper  # noqa: F401
    except Exception:  # noqa: BLE001
        return Check("speech recognition", UNAVAILABLE, "faster-whisper is not installed", core=True)
    try:
        gpus = ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001
        gpus = 0
    cpu_only = os.environ.get("JARVIS_STT_DEVICE", "").lower() == "cpu"
    where = "processor" if cpu_only or not gpus else "GPU, falling back to the processor when the card is full"
    return Check("speech recognition", READY, f"local faster-whisper on the {where}", {"cuda_devices": gpus}, core=True)


def check_tts() -> Check:
    try:
        from .audio import kokoro_tts
        from .config import CONFIG

        with _quiet_stderr():       # onnxruntime prints device-discovery warnings on import
            present = kokoro_tts.available()
        if present:
            return Check("speech output", READY, "Kokoro (Piper as backup)")
        if Path(CONFIG.piper_model).exists():
            return Check("speech output", DEGRADED, "Piper only — Kokoro weights are missing")
    except Exception:  # noqa: BLE001
        pass
    return Check("speech output", UNAVAILABLE, "no voice model found", core=True)


def check_models() -> list[Check]:
    checks = []
    tags = _get("http://127.0.0.1:11434/api/tags", timeout=2)
    models = [m.get("name") for m in (tags or {}).get("models", [])] if tags else []
    checks.append(Check("local model (Ollama)", READY if models else DEGRADED,
                        f"{len(models)} model(s)" if models else "Ollama is not answering — cloud models only",
                        {"models": models[:8]}))
    prov = _get(f"http://127.0.0.1:{PORT}/providers") or {}
    summary = prov.get("summary", "")
    lines = [ln for ln in summary.splitlines() if "·" in ln and not ln.startswith("ollama")]
    ok = [ln.split(":")[0] for ln in lines if ": ok" in ln]
    bad = {ln.split(":")[0]: ln.split(":", 1)[1].strip()[:80] for ln in lines if ": ok" not in ln}
    keys = {name: bool(os.environ.get(var)) for name, var in
            (("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("openai", "OPENAI_API_KEY"))}
    if not prov:
        checks.append(Check("cloud models", UNAVAILABLE, "the backend is not answering", {"keys_set": keys}))
    elif prov.get("strong_available") and not bad:
        checks.append(Check("cloud models", READY, f"{len(ok)} model(s) ok", {"ok": ok, "keys_set": keys}))
    elif prov.get("strong_available"):
        checks.append(Check("cloud models", DEGRADED, f"{len(ok)} ok; unavailable: " + "; ".join(
            f"{k} ({v})" for k, v in bad.items()), {"ok": ok, "failing": bad, "keys_set": keys}))
    else:
        checks.append(Check("cloud models", DEGRADED if models else UNAVAILABLE,
                            "no strong model — answers come from the local model, and say so",
                            {"failing": bad, "keys_set": keys}))
    return checks


def check_brain() -> Check:
    """Which Daily Brain routes can answer now, and which are degraded — from /brain/setup, which
    makes no model call and never returns a key."""
    st = _get(f"http://127.0.0.1:{PORT}/brain/setup")
    if st is None:
        if _get(f"http://127.0.0.1:{PORT}/health") is not None:
            return Check("Daily Brain", DISABLED, "the running backend has no Daily Brain (an older build)")
        return Check("Daily Brain", UNAVAILABLE, "the backend is not answering")
    routes = st.get("routes") or {}
    ok = sorted(r for r, v in routes.items() if v.get("ok"))
    down = {r: str(v.get("why", ""))[:90] for r, v in routes.items() if not v.get("ok")}
    unhealthy = sorted(p["id"] for p in st.get("providers", []) if p.get("enabled") and
                       str(p.get("health", "")).startswith(("paused", "unavailable")))
    data = {"routes_ok": ok, "routes_down": down, "providers_unhealthy": unhealthy}
    if "chat" not in ok:
        return Check("Daily Brain", DEGRADED if st else UNAVAILABLE,
                     "no model can answer questions right now — commands still work", data)
    if down or unhealthy:
        return Check("Daily Brain", DEGRADED, "working; " + "; ".join(
            [f"{r} unavailable" for r in down] + [f"{p} paused" for p in unhealthy]), data)
    return Check("Daily Brain", READY, "every route has a model: " + ", ".join(ok), data)


def check_whatsapp() -> Check:
    st = _get(f"http://127.0.0.1:{WA_PORT}/status")
    if st is None:
        return Check("WhatsApp", UNAVAILABLE, "the bridge is not answering — messages via WhatsApp are off")
    if not st.get("connected"):
        return Check("WhatsApp", USER, "bridge up, not linked — run scripts/relink-whatsapp.sh")
    return Check("WhatsApp", READY, "connected (sending still needs your approval every time)")


def check_browser() -> Check:
    try:
        from .integrations import marionette, web_browser

        name = web_browser.preferred()
        drivable = web_browser.can_be_driven(name)
        if web_browser.family(name) == "firefox":
            live = marionette.reachable(timeout=0.5)
        else:
            from .integrations import browser

            live = browser.is_running()
    except Exception as exc:  # noqa: BLE001
        return Check("browser control", DEGRADED, f"could not check ({type(exc).__name__})")
    if live:
        return Check("browser control", READY, f"{name}: controllable now")
    if drivable:
        return Check("browser control", DEGRADED, f"{name}: not running with control; screen reading is the fallback")
    return Check("browser control", USER, f"{name} cannot be driven as installed")


def check_desktop() -> list[Check]:
    session = os.environ.get("XDG_SESSION_TYPE") or ("wayland" if os.environ.get("WAYLAND_DISPLAY") else
                                                     "x11" if os.environ.get("DISPLAY") else "none")
    a11y = "org.a11y.Bus" in _run(["busctl", "--user", "list", "--no-pager"])
    return [Check("display session", READY if session in {"wayland", "x11"} else DEGRADED, session),
            Check("AT-SPI", READY if a11y else DEGRADED,
                  "accessibility bus present" if a11y else "no accessibility bus — screen reading falls back to OCR")]


def check_phone() -> Check:
    if not shutil.which("kdeconnect-cli"):
        return Check("phone link", DISABLED, "KDE Connect is not installed")
    reachable = [x for x in _run(["kdeconnect-cli", "-a", "--id-only"], timeout=5).split() if x]
    if not reachable:
        return Check("phone link", DEGRADED, "no paired phone reachable — call and SMS announcements are off")
    return Check("phone link", READY, f"{len(reachable)} phone(s) reachable; calls are announced and logged "
                                      "(answering calls is not possible over this link)",
                 {"phones": len(reachable)})


def check_notifications(phone: Check) -> Check:
    try:
        from . import preferences

        on = preferences.notifications_enabled()
    except Exception:  # noqa: BLE001
        on = True
    if not on:
        return Check("notification readouts", DISABLED, "muted by your setting")
    return Check("notification readouts", READY if phone.status == READY else DEGRADED,
                 "on" + ("" if phone.status == READY else " — but no phone is reachable"))


def check_approvals() -> Check:
    ap = _get(f"http://127.0.0.1:{PORT}/approvals")
    if ap is None:
        return Check("approvals", UNAVAILABLE, "the backend is not answering")
    pending = ap.get("pending") or []
    kinds = sorted({p.get("kind", "?") for p in pending})
    jobs = (_get(f"http://127.0.0.1:{PORT}/coding/jobs") or {}).get("jobs", [])
    running = [j for j in jobs if j.get("status") in {"queued", "running"}]
    return Check("approvals and pending side effects", READY,
                 f"{len(pending)} waiting for a yes{(' (' + ', '.join(kinds) + ')') if kinds else ''}; "
                 f"{len(running)} coding job(s) running", {"pending": len(pending), "kinds": kinds,
                                                           "coding_running": len(running)})


def check_away() -> Check:
    try:
        from .away_mode import engine
        from .away_mode.session import ACTIVE, Store
        from .config import CONFIG

        store = Store(CONFIG)
        session = (store.read().get("session") or {})
        killed = engine.kill_switch(store)
    except Exception as exc:  # noqa: BLE001
        return Check("away mode", DEGRADED, f"could not read its state ({type(exc).__name__})")
    if session.get("status") == ACTIVE:
        return Check("away mode", READY, "ON — replying on your behalf" + (" (but the stop is set)" if killed else ""),
                     {"active": True, "stopped": killed})
    return Check("away mode", DISABLED, "off" + ("; emergency stop set" if killed else ""),
                 {"active": False, "stopped": killed})


def check_selfrepair() -> Check:
    try:
        from .selfrepair import editor, jobs, policy, sandbox

        if policy.disabled():
            return Check("self-repair", DISABLED, "JARVIS_SELF_REPAIR_DISABLED is set; settings still work")
        live = [j for j in jobs.JobStore().all() if not j.terminal]
        agent = editor.default_editor()
        wall = sandbox.bwrap_works()
    except Exception as exc:  # noqa: BLE001
        return Check("self-repair", DEGRADED, f"could not check ({type(exc).__name__})")
    data = {"jobs_open": len(live), "coding_agent": bool(agent), "sandbox": wall}
    if not wall:
        return Check("self-repair", DEGRADED, "no working sandbox — repairs refuse to run tests", data)
    if not agent:
        return Check("self-repair", DEGRADED, "no coding agent — reports are recorded, not repaired", data)
    return Check("self-repair", READY, f"{len(live)} open job(s); sandbox and coding agent available", data)


def check_resources() -> list[Check]:
    checks = []
    for label, path in (("disk (repo)", Path(__file__).resolve().parent.parent), ("disk (home)", Path.home())):
        u = shutil.disk_usage(path)
        free = u.free / 1024 ** 3
        checks.append(Check(label, READY if free > 5 else DEGRADED if free > 1 else USER, f"{free:.1f} GB free"))
    mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
    avail = int(mem["MemAvailable"].split()[0]) / 1024 ** 2
    total = int(mem["MemTotal"].split()[0]) / 1024 ** 2
    checks.append(Check("memory", READY if avail > 2 else DEGRADED, f"{avail:.1f} of {total:.1f} GB available"))
    checks.append(check_gpu())
    return checks


def check_gpu() -> Check:
    has_card = "nvidia" in _run(["lspci"]).lower()
    smi = _run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])
    try:
        used, total_v = (int(x) for x in smi.strip().splitlines()[0].split(",")[:2])
    except (ValueError, IndexError):
        if not has_card:
            return Check("GPU", DISABLED, "no NVIDIA card; everything runs on the processor")
        kernel = os.uname().release
        return Check("GPU", USER, f"the NVIDIA driver is not loaded (kernel {kernel}) — speech recognition and "
                                  "the local model run on the processor, slower. Reinstall the driver's DKMS "
                                  "module for this kernel, or boot the previous kernel.", {"driver": False})
    free = total_v - used
    return Check("GPU", READY if free > 600 else DEGRADED,
                 f"{used} of {total_v} MiB used" + ("" if free > 600 else " — speech recognition will use the processor"),
                 {"used_mib": used, "total_mib": total_v, "driver": True})


def check_crashes(services: list[Check]) -> Check:
    counts = {}
    for c in services:
        if c.status == DISABLED:
            continue
        log = _run(["journalctl", "--user", "-u", f"{c.name}.service", "--since", "-24h", "-o", "cat", "--no-pager"],
                   timeout=15)
        counts[c.name] = {"tracebacks_24h": log.count("Traceback (most recent call last)"),
                          "restarts": c.data.get("restarts", 0)}
    worst = max((v["tracebacks_24h"] for v in counts.values()), default=0)
    return Check("crashes", READY if worst == 0 else DEGRADED,
                 "no tracebacks in 24 h" if worst == 0 else f"up to {worst} traceback(s) in 24 h — journalctl --user -u <unit> to read them",
                 counts)


def check_deploy() -> Check:
    try:
        from . import deploy

        s = deploy.classify()
    except Exception as exc:  # noqa: BLE001
        return Check("deployment", DEGRADED, f"could not read ({type(exc).__name__})")
    # A waiting release is not "ready": fixes pushed to it do nothing until a restart takes them,
    # and a reboot does not. On 7 October the voice fixes sat unloaded behind exactly this, while
    # this line said ready.
    status = {"release": READY, "local-repair": READY, "update": DEGRADED, "pinned": DEGRADED,
              "local-commits": DEGRADED, "dirty": USER, "diverged": USER}.get(s.kind, DEGRADED)
    message = s.message
    if s.kind == "update":
        message = (f"running {s.head[:12]}, {s.behind} commit(s) behind the release; "
                   "`jarvis restart` takes them")
    return Check("deployment", status, f"{s.kind}: {message}", {"head": s.head[:12], "kind": s.kind,
                                                                "behind": s.behind, "actions": s.actions})


# ------------------------------------------------------------------------------ run
def run_all() -> dict:
    started = time.monotonic()
    head = ""
    try:
        from . import deploy

        head = deploy._sha(deploy.REPO, "HEAD")
    except Exception:  # noqa: BLE001
        pass
    services = check_services(head)
    phone = check_phone()
    groups: list[Callable[[], object]] = [
        lambda: check_deploy(), lambda: check_ports(services), lambda: check_overlay(head), check_voice, check_microphone, check_stt,
        check_tts, check_models, check_brain, check_whatsapp, check_browser, check_desktop, lambda: phone,
        lambda: check_notifications(phone), check_approvals, check_away, check_selfrepair, check_resources,
        lambda: check_crashes(services),
    ]
    checks: list[Check] = list(services)
    for fn in groups:
        try:
            got = fn()
        except Exception as exc:  # noqa: BLE001 — one broken probe must not hide the rest
            got = Check(getattr(fn, "__name__", "check"), DEGRADED, f"probe failed ({type(exc).__name__})")
        checks += got if isinstance(got, list) else [got]
    core_down = [c for c in checks if c.core and c.status == UNAVAILABLE]
    backend_down = any(c.name == "jarvis-backend" and c.status != READY and c.status != DEGRADED for c in checks)
    worst = UNAVAILABLE if backend_down or core_down else (
        USER if any(c.status == USER for c in checks) else
        DEGRADED if any(c.status in {DEGRADED, UNAVAILABLE} for c in checks) else READY)
    return {"overall": worst, "commit": head[:12], "took_s": round(time.monotonic() - started, 2),
            "checks": [asdict(c) for c in checks]}


_ICON = {READY: "✅", DEGRADED: "🟡", UNAVAILABLE: "❌", DISABLED: "⚪", USER: "👉"}


def summary(report: dict) -> str:
    lines = [f"Jarvis {report['commit']}: {report['overall'].upper()}  ({report['took_s']} s)"]
    for c in report["checks"]:
        lines.append(f"  {_ICON.get(c['status'], '?')} {c['name']:<34} {c['detail']}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="jarvis doctor")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    report = run_all()
    print(json.dumps(report, indent=1) if a.json else summary(report))
    return 0 if report["overall"] != UNAVAILABLE else 1


if __name__ == "__main__":
    sys.exit(main())
