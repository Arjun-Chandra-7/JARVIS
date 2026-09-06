"""Authenticated phone API. Keep the default listener local; use Tailscale for remote access."""
from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/mobile")
_control_lock = asyncio.Lock()


def token() -> str:
    value = os.environ.get("JARVIS_MOBILE_TOKEN", "").strip()
    if value:
        return value
    try:
        return Path("~/.config/jarvis/mobile-token").expanduser().read_text().strip()
    except OSError:
        return ""


async def authorize(request: Request, call_next):
    local = request.client and request.client.host in {"127.0.0.1", "::1", "testclient"}
    forwarded = any(h in request.headers for h in ("x-forwarded-for", "tailscale-user-login"))
    protected = request.url.path.startswith("/mobile") or not local or forwarded
    if protected:
        expected = token()
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not expected or not hmac.compare_digest(supplied, expected):
            return JSONResponse({"detail": "Phone token required"}, status_code=401)
    elif request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:8770", "http://localhost:8770", "http://127.0.0.1:8765", "http://localhost:8765"}:
            return JSONResponse({"detail": "Untrusted origin"}, status_code=403)
    return await call_next(request)


class Control(BaseModel):
    action: Literal["move", "click", "keys", "type", "scroll", "copy", "paste"]
    x: int = Field(0, ge=0, le=32768)
    y: int = Field(0, ge=0, le=32768)
    text: str = Field("", max_length=20000)
    button: Literal["left", "right", "middle"] = "left"
    double: bool = False
    direction: Literal["up", "down"] = "down"
    amount: int = Field(1, ge=1, le=30)


@router.post("/control")
async def control(c: Control):
    from .integrations import desktop_control as desktop
    if not desktop.available():
        raise HTTPException(503, "Desktop input unavailable; check ydotoold and display session")
    actions = {
        "move": lambda: desktop.move(c.x, c.y),
        "click": lambda: desktop.click(c.button, c.double),
        "keys": lambda: desktop.press_keys(c.text),
        "type": lambda: desktop.type_text(c.text),
        "scroll": lambda: desktop.scroll(c.direction, c.amount),
        "copy": lambda: desktop.press_keys("ctrl+c"),
        "paste": lambda: desktop.press_keys("ctrl+v"),
    }
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
