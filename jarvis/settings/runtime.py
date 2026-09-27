"""Getting a setting into the running components, and hearing back that it arrived.

Writing a value is not applying it. Each component that owns settings reports what it is
*actually* doing, stamped with the preferences revision it has seen:

    overlay  applies the change in the page, then measures it — how many CSS animations are
             still running, the motion mode on the document, its opacity, whether the window
             is shown — and POSTs that to /settings/effective
    voice    a watcher thread in the voice process re-reads the file, pushes the values into
             the live objects (the Kokoro speed factor, the conversation's follow-up window),
             and reports the numbers those objects now hold
    backend, away, teach
             live in the process that handles the command, so the check reads the very function
             the component consults

Reports are small JSON files in $XDG_RUNTIME_DIR, so any process can read any other's.

A verification is "verified" (the component reported the new value), "unconfirmed" (it did not
report — usually because it is not running — so the change is saved but unproven) or "failed"
(it reported something else).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .registry import SETTINGS, Setting, snapshot

OVERLAY_KEYS = ("overlay.animations", "overlay.visible", "overlay.intensity", "motion.reduced")
_WAIT = float(os.environ.get("JARVIS_SETTINGS_VERIFY_TIMEOUT", "3.0"))
DEFAULT_TIMEOUT = {"overlay": _WAIT, "voice": _WAIT}


@dataclass
class Verification:
    status: str                     # verified | unconfirmed | failed
    detail: str = ""
    effective: Any = None
    observed: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------ reports
def _runtime_dir() -> Path:
    return Path(os.environ.get("JARVIS_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR") or "/tmp")


def report_path(component: str) -> Path:
    safe = "".join(ch for ch in component if ch.isalnum() or ch in "-_")[:32] or "unknown"
    return _runtime_dir() / f"jarvis-effective-{safe}.json"


def report(component: str, revision: int, values: dict, observed: Optional[dict] = None) -> None:
    """Record what `component` is actually running with. Never raises."""
    row = {"component": component, "revision": int(revision), "values": dict(values),
           "observed": dict(observed or {}), "ts": time.time(), "pid": os.getpid()}
    try:
        path = report_path(component)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(row, default=str))
        os.replace(tmp, path)
    except OSError:
        pass


def read_report(component: str) -> Optional[dict]:
    try:
        data = json.loads(report_path(component).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def wait_for(component: str, revision: int, timeout: float, poll: float = 0.1) -> Optional[dict]:
    """The component's first report at or after `revision`, or None if none came in time."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        row = read_report(component)
        if row and int(row.get("revision", -1)) >= revision:
            return row
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


# ------------------------------------------------------------------------------ transport
def _emit(kind: str, text: str) -> bool:
    """Put an event on the backend's SSE stream. Off in tests (JARVIS_SETTINGS_EMIT=0)."""
    if os.environ.get("JARVIS_SETTINGS_EMIT", "1") == "0":
        return False
    port = os.environ.get("JARVIS_WEB_PORT", "8770")
    body = json.dumps({"kind": kind, "text": text}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/emit", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return 200 <= response.status < 300
    except OSError:
        return False


def overlay_state(revision: int) -> dict:
    values = snapshot()
    return {"revision": revision, "values": {k: values[k] for k in OVERLAY_KEYS}}


# ------------------------------------------------------------------------------ apply
def apply(setting: Setting, value: Any, revision: int) -> None:
    """Push a change towards whoever owns it. Never raises; verify() says whether it landed."""
    try:
        if setting.component == "overlay":
            _emit("settings", json.dumps(overlay_state(revision)))
            if setting.id == "motion.reduced":
                _teach_theme_update({"reduced_motion": bool(value)})
        elif setting.component == "away":
            _apply_away(bool(value))
        elif setting.component == "teach":
            _teach_theme_update({"glow": float(value)})
        # voice: its watcher reads the file; backend: read on every turn.
    except Exception:  # noqa: BLE001 — the verification reports the failure honestly
        pass


def _teach_theme_update(theme: dict) -> bool:
    """Restyle a lesson already on screen. Best effort: the next drawing uses it either way."""
    try:
        from ..teach import bus

        if os.environ.get("JARVIS_SETTINGS_EMIT", "1") == "0":
            return False
        overlay = bus.overlay()
        if not overlay.visible:        # nothing on screen: the next scene.create carries it
            return False
        return overlay.send([{"op": "scene.update", "theme": theme}])
    except Exception:  # noqa: BLE001
        return False


_KILL_MARK = "set by the away.replies setting\n"


def _away_store():
    from ..away_mode.session import Store
    from ..config import CONFIG

    return Store(CONFIG)


def _apply_away(allowed: bool) -> None:
    from ..away_mode import control
    from ..config import CONFIG

    store = _away_store()
    kill = store.dir / "KILL"
    if not allowed:
        store.dir.mkdir(parents=True, exist_ok=True)
        if not kill.exists():
            kill.write_text(_KILL_MARK)
        control.end(CONFIG, store=store)
    elif kill.exists() and kill.read_text() == _KILL_MARK:
        # Only lift a stop this setting put there; a KILL file placed by hand stays.
        kill.unlink()


# ------------------------------------------------------------------------------ verify
def verify(setting: Setting, value: Any, revision: int, *, timeout: Optional[float] = None) -> Verification:
    try:
        if setting.component == "overlay":
            return _verify_overlay(setting, value, revision,
                                   DEFAULT_TIMEOUT["overlay"] if timeout is None else timeout)
        if setting.component == "voice":
            return _verify_voice(setting, value, revision,
                                 DEFAULT_TIMEOUT["voice"] if timeout is None else timeout)
        if setting.component == "away":
            return _verify_away(bool(value))
        if setting.component == "teach":
            return _verify_teach(float(value))
        if setting.component == "backend":
            return _verify_backend(setting, value)
    except Exception as exc:  # noqa: BLE001
        return Verification("failed", f"the check itself failed ({type(exc).__name__})")
    return Verification("unconfirmed", "nothing reports this setting")


def _verify_overlay(setting: Setting, value: Any, revision: int, timeout: float) -> Verification:
    row = wait_for("overlay", revision, timeout)
    if row is None:
        return Verification("unconfirmed", "the overlay didn't answer — it may not be open")
    seen = row.get("observed") or {}
    effective = (row.get("values") or {}).get(setting.id)
    if setting.id == "overlay.animations":
        running = seen.get("running_animations")
        if value is False:
            ok = seen.get("motion") == "off" and running == 0
            detail = f"{running} animations running" if running is not None else "no measurement"
        else:
            ok = seen.get("motion") in {"full", "reduced"}
            detail = f"motion mode {seen.get('motion')}"
        return Verification("verified" if ok else "failed", detail, effective, seen)
    if setting.id == "motion.reduced":
        want = "reduced" if value else ("off" if not snapshot()["overlay.animations"] else "full")
        ok = seen.get("motion") == want
        return Verification("verified" if ok else "failed", f"motion mode {seen.get('motion')}", effective, seen)
    if setting.id == "overlay.visible":
        ok = seen.get("visible") is bool(value)
        return Verification("verified" if ok else "failed", f"visible={seen.get('visible')}", effective, seen)
    if setting.id == "overlay.intensity":
        got = seen.get("intensity")
        ok = isinstance(got, (int, float)) and abs(float(got) - float(value)) < 0.02
        return Verification("verified" if ok else "failed", f"intensity {got}", effective, seen)
    return Verification("unconfirmed", "no overlay check for this setting")


def _verify_voice(setting: Setting, value: Any, revision: int, timeout: float) -> Verification:
    row = wait_for("voice", revision, timeout)
    if row is None:
        return Verification("unconfirmed", "the voice service didn't answer — it may be stopped "
                                           "or still running older code")
    effective = (row.get("values") or {}).get(setting.id)
    ok = effective == value or (
        isinstance(effective, (int, float)) and isinstance(value, (int, float))
        and not isinstance(value, bool) and abs(float(effective) - float(value)) < 1e-6)
    detail = ""
    if setting.id == "voice.speed":
        engine = (row.get("observed") or {}).get("engine", "")
        rate = (row.get("observed") or {}).get("neutral_speed")
        detail = f"{engine} now reads a normal line at speed {rate}" if rate is not None else ""
    elif setting.id == "voice.follow_up_s":
        detail = f"follow-up window {(row.get('observed') or {}).get('window_s')} s"
    return Verification("verified" if ok else "failed", detail, effective, row.get("observed") or {})


def _verify_away(allowed: bool) -> Verification:
    from ..away_mode import engine
    from ..away_mode.session import ACTIVE

    store = _away_store()
    stopped = engine.kill_switch(store)
    state = store.read()
    running = bool((state.get("session") or {}).get("status") == ACTIVE)
    if not allowed:
        ok = stopped and not running
        return Verification("verified" if ok else "failed",
                            "no away session is running and replies are blocked" if ok
                            else "away mode is still able to reply", not stopped)
    ok = not stopped
    return Verification("verified" if ok else "failed",
                        "away replies are allowed again (away mode itself is still off)" if ok
                        else "a stop placed outside this setting is still in force", not stopped)


def teach_theme() -> dict:
    """The owner's part of every lesson's theme; scene.create merges it in."""
    values = snapshot()
    return {"glow": float(values["teach.glow"]), **({"reduced_motion": True} if values["motion.reduced"] else {})}


def _verify_teach(value: float) -> Verification:
    from ..teach import scene

    theme = scene.create("verify", "primary").get("theme") or {}
    got = theme.get("glow")
    ok = isinstance(got, (int, float)) and abs(float(got) - value) < 1e-6
    return Verification("verified" if ok else "failed", f"the next drawing's pen glow is {got}", got)


def verbosity_instruction() -> str:
    """What the brain is told about length, from the setting — read on every turn."""
    level = snapshot()["voice.verbosity"]
    return {
        "brief": "Answer in one or two short sentences unless asked for more.",
        "detailed": "Give fuller answers, with the reasoning and the relevant detail.",
    }.get(level, "")


def _verify_backend(setting: Setting, value: Any) -> Verification:
    if setting.id == "voice.verbosity":
        text = verbosity_instruction()
        want = {"brief": "short", "detailed": "fuller"}.get(value)
        ok = (want in text) if want else text == ""
        return Verification("verified" if ok else "failed", "the next answer uses it", value)
    return Verification("unconfirmed", "no check for this setting")


def known(setting_id: str) -> bool:
    return setting_id in SETTINGS
