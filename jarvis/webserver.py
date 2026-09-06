"""FastAPI server: serves the WebGL HUD (webui/) and bridges it to Jarvis's brain."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent.factory import make_agent
from .config import CONFIG
from . import hud_state
from .memory.vault import ensure_vault

if getattr(sys, "frozen", False):
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    WEBUI = bundle_dir / "webui"
else:
    WEBUI = Path(__file__).resolve().parent.parent / "webui"

_agent: dict = {"a": None}
_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    from .agent import ai_researcher
    ai_researcher.start_research_agent()
    agent = make_agent(CONFIG, mode="text", confirm_fn=None, on_tool=None)
    await agent.__aenter__()
    _agent["a"] = agent
    from .agent import pa_daemon
    pa_daemon.start(CONFIG)
    from .integrations import coding_jobs
    loop = asyncio.get_running_loop()

    def _on_coding(update: dict) -> None:  # subscriber callbacks may run off the loop thread
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_emit("coding_job", json.dumps(update.get("job", update)))))

    _unsub = coding_jobs.subscribe(_on_coding)
    watch = asyncio.create_task(_coding_watch())
    contacts_task = asyncio.create_task(_contacts_ingest())
    try:
        yield
    finally:
        watch.cancel()
        contacts_task.cancel()
        _unsub()
        pa_daemon.stop(CONFIG)
        await agent.__aexit__(None, None, None)


async def _contacts_ingest() -> None:
    """Fold new WhatsApp history into the private contact index every 15 min (best effort)."""
    from .memory import contacts_index

    def _llm(prompt: str) -> str:
        base_url, key, model = CONFIG.llm_params()
        if not key:
            return ""
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=key, max_retries=0, timeout=30)
        r = client.chat.completions.create(model=model, temperature=0.2, max_tokens=220,
                                           messages=[{"role": "user", "content": prompt}])
        return (r.choices[0].message.content or "").strip()

    llm = _llm if CONFIG.brain in {"gemini", "groq"} else None
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(contacts_index.ingest, CONFIG, None, 800, llm)
        except Exception:  # noqa: BLE001 - ingestion is best-effort
            pass
        await asyncio.sleep(900)


async def _coding_watch() -> None:
    """Emit a HUD event whenever any coding job (Jarvis-started or externally detected) changes state."""
    from .integrations import coding_jobs
    manager = coding_jobs.get_manager()
    last: dict[str, str] = {}
    while True:
        try:
            manager.sync_external()
            for job in manager.list_jobs(50):
                if last.get(job["id"]) != job["status"]:
                    last[job["id"]] = job["status"]
                    await _emit("coding_job", json.dumps(job))
        except Exception:  # noqa: BLE001 - a HUD feed must never crash the server
            pass
        await asyncio.sleep(3)


app = FastAPI(lifespan=lifespan)
from .mobile import authorize, router as mobile_router
app.middleware("http")(authorize)
app.include_router(mobile_router)

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# Security: Restrict CORS strictly to local HUD clients to prevent external web page CSRF attacks
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8770", "http://localhost:8770", "http://127.0.0.1:8765", "http://localhost:8765"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Chat(BaseModel):
    message: str = Field(min_length=1, max_length=30000)
    session_id: str = Field(default="local", max_length=80)


@app.post("/chat")
async def chat(c: Chat):
    agent = _agent["a"]
    if agent is None:
        return {"reply": "Brain still booting, sir — one moment."}
    hud_state.log_turn("you", c.message)
    async with _lock:
        try:
            agent.command_session = c.session_id
            reply = await agent.send(c.message)
        except Exception as exc:  # noqa: BLE001
            reply = f"[error] {exc}"
    hud_state.log_turn("jarvis", reply)
    return {"reply": reply}


@app.get("/coding/jobs")
async def coding_jobs():
    from .integrations.coding_jobs import list_jobs
    return {"jobs": list_jobs()}


class CodingEvent(BaseModel):
    id: str = Field(max_length=100)
    provider: str = "codex"
    workspace: str = ""
    status: str = "running"
    output: str = Field("", max_length=12000)


@app.post("/coding/events")
async def coding_event(event: CodingEvent):
    from .integrations.coding_jobs import get_manager
    from fastapi import HTTPException
    manager = get_manager()
    try:
        if not manager.get_job(event.id):
            manager.create_external("External editor prompt", event.workspace, event.provider, external_id=event.id)
        return manager.record_event(event.id, event.status, output=event.output)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/meet/status")
async def meet_status():
    from .integrations.meet_bot import status
    return status()


@app.get("/meet/summary")
async def meet_summary():
    from .integrations.meet_bot import status
    s = status()
    return {"state": s["state"], "summary": s.get("summary", ""), "vault_note": s.get("vault_note")}


@app.get("/notifications")
async def notification_status():
    from .preferences import notifications_enabled
    return {"enabled": notifications_enabled()}


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


@app.get("/spotify")
async def spotify_status():
    import subprocess
    import urllib.request
    import urllib.parse
    
    try:
        # Check if spotify is running and get info
        status = subprocess.check_output(["systemd-run", "--user", "--pipe", "--quiet", "playerctl", "-p", "spotify", "status"], text=True).strip()
        if status not in ("Playing", "Paused"):
            return {"playing": False}
        
        artist = subprocess.check_output(["systemd-run", "--user", "--pipe", "--quiet", "playerctl", "-p", "spotify", "metadata", "artist"], text=True).strip()
        title = subprocess.check_output(["systemd-run", "--user", "--pipe", "--quiet", "playerctl", "-p", "spotify", "metadata", "title"], text=True).strip()
        art = subprocess.check_output(["systemd-run", "--user", "--pipe", "--quiet", "playerctl", "-p", "spotify", "metadata", "mpris:artUrl"], text=True).strip()
        
        # Get position in seconds
        pos_str = subprocess.check_output(["systemd-run", "--user", "--pipe", "--quiet", "playerctl", "-p", "spotify", "position"], text=True).strip()
        position = float(pos_str)
        
        # Fetch synced lyrics from LRCLIB if we don't have them cached
        # Simple cache on the title to avoid spamming the API
        if getattr(spotify_status, "last_title", None) != title:
            spotify_status.last_title = title
            spotify_status.lyrics = []
            
            try:
                url = f"https://lrclib.net/api/get?track_name={urllib.parse.quote(title)}&artist_name={urllib.parse.quote(artist)}"
                req = urllib.request.Request(url, headers={'User-Agent': 'JarvisAssistant/1.0'})
                with urllib.request.urlopen(req, timeout=2) as r:
                    data = json.loads(r.read().decode())
                    if data.get("syncedLyrics"):
                        # Parse LRC format
                        lines = data["syncedLyrics"].split('\n')
                        parsed = []
                        for line in lines:
                            if line.startswith('[') and ']' in line:
                                time_str = line[1:line.find(']')]
                                text = line[line.find(']')+1:].strip()
                                if text:
                                    try:
                                        m, s = time_str.split(':')
                                        sec = int(m) * 60 + float(s)
                                        parsed.append({"time": sec, "text": text})
                                    except: pass
                        spotify_status.lyrics = parsed
            except Exception as e:
                pass

        return {
            "playing": status == "Playing",
            "artist": artist,
            "title": title,
            "art": art,
            "position": position,
            "lyrics": getattr(spotify_status, "lyrics", [])
        }
    except Exception:
        return {"playing": False}
        
@app.post("/spotify/control")
async def spotify_control(c: Chat):
    import subprocess
    cmd = c.message
    if cmd in ("play", "pause", "play-pause", "next", "previous"):
        try:
            subprocess.run(["systemd-run", "--user", "--pipe", "--quiet", "playerctl", "-p", "spotify", cmd])
        except Exception:
            pass
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


@app.get("/nearby")
async def nearby():
    from . import nearby as _nb

    try:
        return _nb.snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@app.get("/sonar")
async def sonar():
    """Live inaudible acoustic active radar / physical body sonar."""
    from . import sonar as _sonar

    try:
        return _sonar.get_sonar_snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@app.get("/whatsapp/inbox")
async def whatsapp_inbox():
    """Proxy the live WhatsApp bridge's recent-message feed for the HUD."""
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/inbox", timeout=2) as r:
            return {"messages": json.loads(r.read().decode("utf-8"))}
    except Exception as exc:  # noqa: BLE001
        return {"messages": [], "error": str(exc)}


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


# static assets (completion sound, icons) then the HUD — mounted last so API routes win
_ASSETS = WEBUI.parent / "assets"
if _ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")
app.mount("/", StaticFiles(directory=str(WEBUI), html=True), name="webui")
