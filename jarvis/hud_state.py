"""HUD-facing state helpers: live subsystem health + a persisted conversation log.

Kept deliberately dependency-light and defensive — every probe degrades to a
sensible default instead of raising, so the HUD never goes dark because one
optional integration is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from .config import CONFIG

_STATE_DIR = Path("~/.config/jarvis").expanduser()
_HISTORY = _STATE_DIR / "hud-history.jsonl"
_MAX_HISTORY = 400

_BOOT = time.time()


# --------------------------------------------------------------------------- #
# conversation persistence                                                    #
# --------------------------------------------------------------------------- #
def log_turn(who: str, text: str) -> None:
    """Append one message to the durable HUD history (best-effort)."""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"who": who, "text": text, "t": time.time()}
        with _HISTORY.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _trim_history()
    except Exception:  # noqa: BLE001
        pass


def _trim_history() -> None:
    try:
        lines = _HISTORY.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_HISTORY:
            _HISTORY.write_text("\n".join(lines[-_MAX_HISTORY:]) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def recent_history(limit: int = 40) -> list[dict]:
    try:
        lines = _HISTORY.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return out


def clear_history() -> None:
    try:
        _HISTORY.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# subsystem health                                                            #
# --------------------------------------------------------------------------- #
def _ollama_up() -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _brain_health() -> dict:
    brain = CONFIG.brain
    label = {
        "ollama": f"Ollama · {CONFIG.ollama_model}",
        "gemini": f"Gemini · {CONFIG.gemini_model}",
        "groq": f"Groq · {CONFIG.groq_model}",
        "claude": f"Claude · {CONFIG.model}",
    }.get(brain, brain)
    if brain == "ollama":
        ok = _ollama_up()
    elif brain == "gemini":
        ok = bool(CONFIG.gemini_api_key)
    elif brain == "groq":
        ok = bool(CONFIG.groq_api_key)
    else:
        ok = shutil.which("claude") is not None
    return {"name": "Brain", "label": label, "ok": ok}


def _vault_health() -> dict:
    v = CONFIG.vault_path
    ok = v.exists()
    dirty = None
    if ok and (v / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(v), "status", "--porcelain"],
                capture_output=True, text=True, timeout=4,
            ).stdout.strip()
            dirty = bool(out)
        except Exception:  # noqa: BLE001
            dirty = None
    label = "Obsidian" + (" · uncommitted" if dirty else "")
    return {"name": "Memory", "label": label, "ok": ok}


def _voice_health() -> dict:
    backend = CONFIG.resolved_voice_backend()
    return {"name": "Voice", "label": backend, "ok": True}


def _google_health() -> dict:
    ok = CONFIG.google_token_file.exists()
    return {"name": "Google", "label": "linked" if ok else "not linked", "ok": ok}


def _phone_health() -> dict:
    if shutil.which("kdeconnect-cli") is None:
        return {"name": "Phone", "label": "unavailable", "ok": False}
    # a reachable, paired device counts as connected
    try:
        out = subprocess.run(
            ["kdeconnect-cli", "-a", "--id-only"],
            capture_output=True, text=True, timeout=4,
        ).stdout.strip()
        ok = bool(out)
    except Exception:  # noqa: BLE001
        ok = False
    return {"name": "Phone", "label": "linked" if ok else "no device", "ok": ok}


def _whatsapp_health() -> dict:
    """Probe the live WhatsApp bridge (node service on :8765) — authoritative."""
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8765/status", timeout=1.5) as r:
            connected = bool(json.loads(r.read().decode("utf-8")).get("connected"))
        return {"name": "WhatsApp", "label": "connected" if connected else "linking…", "ok": connected}
    except Exception:  # noqa: BLE001
        return {"name": "WhatsApp", "label": "bridge offline", "ok": False}


_wx_cache: dict = {"t": 0.0, "data": None}


def weather() -> dict | None:
    """Current conditions for CONFIG.city via keyless wttr.in, cached 15 min."""
    now = time.time()
    if _wx_cache["data"] is not None and now - _wx_cache["t"] < 900:
        return _wx_cache["data"]
    try:
        import urllib.parse
        import urllib.request

        city = urllib.parse.quote(CONFIG.city)
        url = f"https://wttr.in/{city}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=4) as r:
            raw = json.loads(r.read().decode("utf-8"))
        cur = raw["current_condition"][0]
        data = {
            "temp_c": int(cur["temp_C"]),
            "feels_c": int(cur["FeelsLikeC"]),
            "desc": cur["weatherDesc"][0]["value"],
            "humidity": int(cur["humidity"]),
            "city": CONFIG.city,
        }
    except Exception:  # noqa: BLE001
        data = None
    _wx_cache.update(t=now, data=data)
    return data


def health() -> dict:
    """Full subsystem snapshot for the HUD SYSTEMS panel + header."""
    systems = [
        _brain_health(),
        _vault_health(),
        _voice_health(),
        _google_health(),
        _phone_health(),
        _whatsapp_health(),
    ]
    brain = systems[0]
    return {
        "systems": systems,
        "brain": brain["label"],
        "brain_ok": brain["ok"],
        "user": CONFIG.user_name,
        "city": CONFIG.city,
        "uptime_s": round(time.time() - _BOOT),
        "online": sum(1 for s in systems if s["ok"]),
        "total": len(systems),
    }
