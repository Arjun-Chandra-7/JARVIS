"""Authenticated phone API. Keep the default listener local; use Tailscale for remote access.

Security model
--------------
* Every ``/mobile/*`` route requires a strong bearer token (``JARVIS_MOBILE_TOKEN`` env or
  ``~/.config/jarvis/mobile-token`` from ``scripts/pair-mobile.py``). There is no localhost
  exemption for these routes and no configured token means *deny* (fail closed).
* Token comparison is constant-time. The token is never logged or placed in a URL.
* Remote exposure is expected to be Tailscale (``tailscale serve``); the desktop-control
  surface is never meant to face the public internet.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/mobile")
_control_lock = asyncio.Lock()
_fail_times: dict[str, list[float]] = {}
_TRUSTED_ORIGINS = {
    "http://127.0.0.1:8770", "http://localhost:8770",
    "http://127.0.0.1:8765", "http://localhost:8765",
}


def token() -> str:
    value = os.environ.get("JARVIS_MOBILE_TOKEN", "").strip()
    if value:
        return value
    try:
        return Path("~/.config/jarvis/mobile-token").expanduser().read_text().strip()
    except OSError:
        return ""


def _bearer(request: Request) -> str:
    raw = request.headers.get("authorization", "")
    return raw[7:].strip() if raw[:7].lower() == "bearer " else raw.strip()


def _rate_limited(client: str) -> bool:
    """Throttle bearer-guessing: >10 failures from one host inside 60 s -> 429."""
    now = time.monotonic()
    hits = [t for t in _fail_times.get(client, []) if now - t < 60]
    _fail_times[client] = hits
    return len(hits) >= 10


async def authorize(request: Request, call_next):
    host = (request.client.host if request.client else "") or ""
    local = host in {"127.0.0.1", "::1", "testclient"}
    forwarded = any(h in request.headers for h in
                    ("x-forwarded-for", "x-forwarded-host", "forwarded", "tailscale-user-login"))
    mobile = request.url.path.startswith("/mobile")
    if mobile or not local or forwarded:
        if _rate_limited(host):
            return JSONResponse({"detail": "Too many attempts"}, status_code=429)
        expected, supplied = token(), _bearer(request)
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            _fail_times.setdefault(host, []).append(time.monotonic())
            return JSONResponse({"detail": "Phone token required"}, status_code=401)
    elif request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in _TRUSTED_ORIGINS:
            return JSONResponse({"detail": "Untrusted origin"}, status_code=403)
    return await call_next(request)


class Control(BaseModel):
    action: Literal["move", "click", "keys", "type", "scroll", "copy", "paste"]
    x: int = Field(0, ge=0, le=32768)
    y: int = Field(0, ge=0, le=32768)
    dx: int = Field(0, ge=-2000, le=2000)
    dy: int = Field(0, ge=-2000, le=2000)
    text: str = Field("", max_length=20000)
    keys: str = Field("", max_length=200)          # alias the phone app may send for a shortcut
    button: Literal["left", "right", "middle"] = "left"
    double: bool = False
    direction: Literal["up", "down"] = "down"
    amount: int = Field(1, ge=1, le=30)


@router.post("/control")
async def control(c: Control):
    from .integrations import desktop_control as desktop
    if not desktop.available():
        raise HTTPException(503, "Desktop input unavailable; check ydotoold and display session")
    combo = c.keys or c.text
    actions = {
        "move": lambda: desktop.move_rel(c.dx, c.dy) if (c.dx or c.dy) else desktop.move(c.x, c.y),
        "click": lambda: desktop.click(c.button, c.double),
        "keys": lambda: desktop.press_keys(combo),
        "type": lambda: desktop.type_text(c.text),
        "scroll": lambda: desktop.scroll(c.direction, c.amount),
        "copy": lambda: desktop.press_keys("ctrl+c"),
        "paste": lambda: desktop.press_keys("ctrl+v"),
    }
    if c.action in {"keys"} and not combo:
        raise HTTPException(400, "keys/text is required for a key action")
    async with _control_lock:
        ok = await asyncio.to_thread(actions[c.action])
    if not ok:
        raise HTTPException(503, "Desktop input failed")
    return {"ok": True}


@router.get("/screen")
async def screen():
    from .vision import screenshot
    path = await asyncio.to_thread(screenshot.capture)
    if not path:
        raise HTTPException(503, "Screen capture unavailable; desktop sharing permission may be needed")
    geom = getattr(screenshot, "_last_geom", {})
    real = geom.get("real", (0, 0))
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store",
                        "X-Screen-Width": str(real[0]), "X-Screen-Height": str(real[1])})


@router.post("/transcribe")
async def transcribe(request: Request):
    from .audio.local_stt import transcribe as recognize
    from .config import CONFIG
    # Mono signed 16-bit little-endian PCM at 16 kHz, at most 60 seconds.
    pcm = bytearray()
    async for chunk in request.stream():
        pcm.extend(chunk)
        if len(pcm) > 16000 * 2 * 60:
            raise HTTPException(413, "Maximum recording length is 60 seconds")
    if not pcm or len(pcm) % 2:
        raise HTTPException(400, "Expected mono PCM16 at 16000 Hz")
    text = await asyncio.to_thread(recognize, bytes(pcm), 16000, CONFIG.whisper_model,
                                   CONFIG.whisper_beam, CONFIG.stt_language, CONFIG.stt_vocabulary)
    return {"text": text}


@router.get("/status")
async def mobile_status():
    """One call the phone can poll: connectivity, current work, and a little system state."""
    from . import hud_state
    from .config import CONFIG
    from .integrations import coding_jobs, meet_bot
    out: dict = {"ok": True}
    try:
        h = hud_state.health()
        out["brain"] = h.get("brain")
        out["online"] = f"{h.get('online', 0)}/{h.get('total', 0)}"
    except Exception:  # noqa: BLE001
        pass
    try:
        from .agent import away
        out["away"] = away.is_away(CONFIG)
    except Exception:  # noqa: BLE001
        out["away"] = False
    try:
        out["meet"] = meet_bot.status().get("state", "idle")
    except Exception:  # noqa: BLE001
        out["meet"] = "idle"
    try:
        jobs = coding_jobs.list_jobs(8)
        out["coding_jobs"] = [{"provider": j["provider"], "status": j["status"],
                               "workspace": (j.get("workspace") or "").split("/")[-1],
                               "summary": j.get("summary", "")[:120]} for j in jobs]
    except Exception:  # noqa: BLE001
        out["coding_jobs"] = []
    try:
        from .integrations import system_stats
        s = system_stats.snapshot()
        out["cpu"] = s.get("cpu_percent")
        out["mem"] = (s.get("mem") or {}).get("percent")
    except Exception:  # noqa: BLE001
        pass
    return out
