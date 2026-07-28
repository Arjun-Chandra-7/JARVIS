"""FastAPI server: serves the WebGL HUD (webui/) and bridges it to Jarvis's brain."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent.factory import make_agent
from .config import CONFIG
from . import hud_state
from .memory.vault import ensure_vault

WEBUI = Path(__file__).resolve().parent.parent / "webui"

_agent: dict = {"a": None}
_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    agent = make_agent(CONFIG, mode="text", confirm_fn=None, on_tool=None)
    await agent.__aenter__()
    _agent["a"] = agent
    try:
        yield
    finally:
        await agent.__aexit__(None, None, None)


app = FastAPI(lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class Chat(BaseModel):
    message: str


@app.post("/chat")
async def chat(c: Chat):
    agent = _agent["a"]
    if agent is None:
        return {"reply": "Brain still booting, sir — one moment."}
    hud_state.log_turn("you", c.message)
    async with _lock:
        try:
            reply = await agent.send(c.message)
        except Exception as exc:  # noqa: BLE001
            reply = f"[error] {exc}"
    hud_state.log_turn("jarvis", reply)
    return {"reply": reply}


# --- live event stream (voice/phone events → browser HUD) ---
_subscribers: set = set()


async def _emit(kind: str, text: str = "") -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait({"kind": kind, "text": text})
        except Exception:  # noqa: BLE001
            _subscribers.discard(q)


class Emit(BaseModel):
    kind: str
    text: str = ""


@app.post("/emit")
async def emit(e: Emit):
    # persist the meaningful spoken turns so the HUD can restore them on reload
    if e.kind == "heard" and e.text:
        hud_state.log_turn("you", e.text)
    elif e.kind == "reply" and e.text:
        hud_state.log_turn("jarvis", e.text)
    await _emit(e.kind, e.text)
    return {"ok": True}


@app.get("/health")
async def health():
    try:
        h = hud_state.health()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    h["booting"] = _agent["a"] is None
    return h


@app.get("/weather")
async def weather():
    return {"weather": hud_state.weather()}


@app.get("/history")
async def history(limit: int = 40):
    return {"messages": hud_state.recent_history(limit)}


@app.delete("/history")
async def wipe_history():
    hud_state.clear_history()
    return {"ok": True}


# Quick-action chips shown in the HUD. Each maps a label → a prompt sent to the brain.
_SUGGESTIONS = [
    {"label": "Catch me up", "icon": "inbox", "say": "What did I miss? Summarise messages, mail and anything important."},
    {"label": "My agenda", "icon": "calendar", "say": "What's on my calendar today and what's my next meeting?"},
    {"label": "Read screen", "icon": "eye", "say": "Take a screenshot and tell me what's on my screen."},
    {"label": "System status", "icon": "activity", "say": "Give me a full system status report."},
    {"label": "Unread mail", "icon": "mail", "say": "Check my email and summarise anything that needs a reply."},
    {"label": "Focus mode", "icon": "moon", "say": "I'm going heads-down. Hold non-urgent notifications and cover my messages."},
]


@app.get("/suggestions")
async def suggestions():
    return {"suggestions": _SUGGESTIONS}


@app.get("/stats")
async def stats():
    from .integrations import system_stats

    try:
        return system_stats.snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@app.get("/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)

    async def gen():
        try:
            yield f"data: {json.dumps({'kind': 'ready', 'text': 'HUD linked'})}\n\n"
            while True:
                item = await q.get()
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# static HUD (index.html at /, app.js at /app.js) — mounted last so routes win
app.mount("/", StaticFiles(directory=str(WEBUI), html=True), name="webui")
