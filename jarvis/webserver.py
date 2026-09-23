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
_providers: dict = {"summary": "not checked yet", "strong": None}


async def _provider_health() -> None:
    """Which models answer, checked once at startup (model lists only — no tokens). A retired
    model or a denied project is said here, once, instead of being discovered on every request."""
    from . import providers
    try:
        results = await asyncio.to_thread(providers.health_check, CONFIG, 8.0)
    except Exception as exc:  # noqa: BLE001 — a health check must never stop the server starting
        _providers.update(summary=f"provider check failed: {type(exc).__name__}", strong=None)
        return
    _providers.update(summary=providers.summary(results),
                      strong=any(h.ok and h.provider.quality == "strong" for h in results))
    print("providers:\n  " + _providers["summary"].replace("\n", "\n  "), flush=True)
    if not _providers["strong"]:
        await _emit("error", "No strong model available — explanations are off. See /providers.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    provider_check = asyncio.create_task(_provider_health())
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
    study_watch = asyncio.create_task(_study_watch())
    contacts_task = asyncio.create_task(_contacts_ingest())
    from .presence import service as presence
    try:
        presence.start(CONFIG)
    except Exception:  # noqa: BLE001 - presence is optional, never block the server on it
        pass
    # The browser extension dials out to us, so the relay has to be listening before the browser
    # is, not after. One idle loopback listener; it costs nothing until something connects, and
    # the extension retries on its own if this fails or Jarvis restarts.
    from .integrations import browser
    try:
        await browser.start_relay()
    except Exception:  # noqa: BLE001 - browser control is optional, like presence
        pass
    try:
        yield
    finally:
        watch.cancel()
        study_watch.cancel()
        contacts_task.cancel()
        _unsub()
        try:
            await browser.relay().stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            presence.service().stop()
        except Exception:  # noqa: BLE001
            pass
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


async def _study_watch() -> None:
    """Enforce study mode even when the separate voice process is not running."""
    from .modes import study

    while True:
        if study.on():
            try:
                await study.enforce()
            except Exception:  # noqa: BLE001
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
    event_id: str = Field(default="", max_length=64)


@app.post("/chat")
async def chat(c: Chat):
    agent = _agent["a"]
    if agent is None:
        return {"reply": "Brain still booting, sir — one moment."}
    from .commands import clean_text
    from .dedupe import CHAT as dedupe
    shown = clean_text(c.message) or c.message.strip()[:400]
    hud_state.log_turn("you", shown)
    await _emit("heard", shown)          # every turn — typed, voice, phone, telegram — hits the HUD
    async with _lock:
        # Checked inside the lock: a duplicate queued behind the original sees it finished.
        again = dedupe.seen(c.message, c.event_id)
        if again is not None:
            from . import route_log
            route_log.record(intent="duplicate", action="skipped")
            return {"reply": again, "duplicate": True}
        try:
            agent.command_session = c.session_id
            reply = await agent.send(c.message)
        except Exception as exc:  # noqa: BLE001
            reply = f"[error] {exc}"
        dedupe.done(c.message, reply, c.event_id)
    hud_state.log_turn("jarvis", reply)
    await _emit("reply", reply)
    return {"reply": reply}


@app.get("/providers")
async def provider_status():
    """The startup provider check, and which breakers are open now."""
    from . import providers
    open_now = {p.id: providers.blocked(p) for p in providers.configured(CONFIG)}
    return {"summary": _providers["summary"], "strong_available": _providers["strong"],
            "paused": {k: {"kind": v["kind"], "until": v["until"]} for k, v in open_now.items() if v}}


@app.get("/approvals")
async def approvals_pending(session_id: str = "local"):
    """What is waiting for a yes, for the overlay to show. Summaries only; never the details."""
    from .approvals import MANAGER
    return {"pending": [{"id": a.id, "kind": a.kind, "summary": a.summary, "expires": a.expires,
                         "fingerprint": a.fingerprint()} for a in MANAGER.pending(session_id)]}


class Decision(BaseModel):
    decision: str = Field(pattern="^(confirm|cancel)$")
    fingerprint: str = Field(default="", max_length=32)


@app.post("/approvals/{action_id}")
async def approvals_decide(action_id: str, d: Decision):
    """An overlay button. The fingerprint it was shown must still match, or nothing runs."""
    from .approvals import MANAGER
    async with _lock:
        if d.decision == "cancel":
            out = MANAGER.cancel(action_id)
        else:
            out = await MANAGER.confirm(action_id, fingerprint=d.fingerprint or None)
    hud_state.log_turn("jarvis", out.message)
    await _emit("reply", out.message)
    return {"status": out.status, "message": out.message}


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


@app.get("/power")
async def power_status():
    from . import power
    return {"asleep": power.asleep(), "since": power.since()}


@app.post("/power")
async def power_set(body: dict):
    from . import power
    action = str(body.get("action", "toggle"))
    target = (not power.asleep()) if action == "toggle" else (action == "sleep")
    power.set_asleep(target)
    await _emit("power", "asleep" if target else "awake")
    return {"asleep": target}


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

@app.get("/projects")
async def projects():
    """The folders you actually work in, newest first.

    Read from disk rather than kept in a list somewhere, so a project started this morning is on
    the HUD this morning without anybody registering it.
    """
    from pathlib import Path

    roots = [Path.home() / "Madara" / "Dev", Path.home() / "Dev", Path.home() / "Projects"]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            try:
                touched = child.stat().st_mtime
            except OSError:
                continue
            found.append({"name": child.name, "path": str(child), "touched": touched})
    found.sort(key=lambda p: p["touched"], reverse=True)
    return {"projects": found}


class OpenProject(BaseModel):
    path: str = Field(max_length=500)


@app.post("/project/open")
async def project_open(req: OpenProject):
    """Open a folder in the editor, and take the terminal there too.

    Doing only the first leaves you in the right code with a shell in the wrong directory, which
    is worse than doing neither — you notice the editor moved and assume everything did.
    """
    import asyncio as _asyncio
    import subprocess
    from pathlib import Path

    folder = Path(req.path).expanduser()
    # Only somewhere that exists, and only a directory. The path arrives from a click, but it
    # arrives over HTTP, and this opens an editor and types into a shell.
    if not folder.is_dir():
        return {"ok": False, "error": "not a folder"}
    allowed = (Path.home() / "Madara" / "Dev", Path.home() / "Dev", Path.home() / "Projects")
    real = folder.resolve()
    if not any(str(real).startswith(str(root.resolve())) for root in allowed if root.exists()):
        return {"ok": False, "error": "outside your project folders"}

    try:
        subprocess.Popen(["code", str(real)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not start the editor: {exc}"}

    # Give the window a moment to come up before typing into its terminal.
    await _asyncio.sleep(2.5)
    moved = False
    try:
        from .coding import vscode
        import shlex

        ready, _ = await _asyncio.to_thread(vscode.ensure_front)
        if ready and await _asyncio.to_thread(vscode.new_terminal):
            moved = await _asyncio.to_thread(vscode.type_line, f"cd {shlex.quote(str(real))}")
    except Exception:  # noqa: BLE001 — the editor still opened; say so honestly
        moved = False
    return {"ok": True, "opened": str(real), "terminal_followed": moved}


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


@app.get("/radar")
async def radar():
    """Fused human presence: camera bearing+range, acoustic range-only, device identity."""
    from .presence import service as presence

    try:
        return presence.snapshot(CONFIG)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "contacts": [], "sensors": []}


@app.get("/sonar")
async def sonar():
    """Deprecated alias for /radar, kept so older HUD builds keep working."""
    return await radar()


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


@app.post("/lens/{kind}")
async def lens_capture(kind: str):
    """Capture one scoped piece of context (selection / clipboard / window / screen).

    Deliberately pull, not push: it runs when asked, the overlay shows exactly what was taken
    before anything is sent, and it applies to a single request.
    """
    from .integrations import lens

    return await asyncio.to_thread(lens.capture, kind)


@app.post("/speech/stop")
async def speech_stop():
    """Stop Jarvis talking. Distinct from cancelling the work that produced the words."""
    from .audio import speech_control

    stopped = speech_control.request_stop()
    return {"ok": True, "stopped": stopped,
            "message": "Stopped speaking." if stopped else "Jarvis was not speaking."}


@app.post("/tasks/cancel")
async def tasks_cancel():
    """Cancel queued background work, and report honestly about anything already running."""
    from .jobs import cancel as jobs_cancel

    return await asyncio.to_thread(jobs_cancel.cancel_all)


@app.get("/memory/search")
async def memory_search(q: str, limit: int = 20):
    from .memory import control

    results = await asyncio.to_thread(control.search, CONFIG.vault_path, q, limit)
    return {"query": q, "results": [m.as_dict() for m in results]}


class MemoryEdit(BaseModel):
    path: str
    snippet: str = ""
    replacement: str = ""


@app.post("/memory/correct")
async def memory_correct(edit: MemoryEdit):
    from .memory import control

    return await asyncio.to_thread(
        control.correct, CONFIG.vault_path, edit.path, edit.snippet, edit.replacement
    )


@app.post("/memory/forget")
async def memory_forget(edit: MemoryEdit):
    from .memory import control

    return await asyncio.to_thread(control.forget, CONFIG.vault_path, edit.path, edit.snippet)


@app.get("/audio")
async def audio_level():
    """Live microphone / speech level for the HUD's audio-reactive visuals.

    Published to tmpfs by the voice process rather than pushed over this server's event stream:
    the event path is a blocking HTTP call made from the thread reading microphone frames, and at
    ~20 Hz that would drop audio. Returns a zeroed idle reading when the value is stale, so the
    HUD never animates a level no microphone is producing.
    """
    from .audio import levels

    return levels.read()


# Set when the process is asked to stop. The event stream below never ends on its own, and
# uvicorn's graceful shutdown waits for every in-flight request to finish, so one connected
# overlay held the backend open until systemd gave up and killed it — ninety seconds, every
# restart, and the service left in a failed state each time.
_shutting_down = asyncio.Event()


@app.on_event("shutdown")
async def _release_streams() -> None:
    _shutting_down.set()


@app.get("/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)

    async def gen():
        try:
            yield f"data: {json.dumps({'kind': 'ready', 'text': 'HUD linked'})}\n\n"
            stopping = asyncio.ensure_future(_shutting_down.wait())
            while not _shutting_down.is_set():
                nxt = asyncio.ensure_future(q.get())
                done, _ = await asyncio.wait({nxt, stopping},
                                             return_when=asyncio.FIRST_COMPLETED)
                if nxt in done:
                    yield f"data: {json.dumps(nxt.result())}\n\n"
                else:
                    nxt.cancel()
                    break
            stopping.cancel()
        finally:
            _subscribers.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# static assets (completion sound, icons) then the HUD — mounted last so API routes win
_ASSETS = WEBUI.parent / "assets"
if _ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")
app.mount("/", StaticFiles(directory=str(WEBUI), html=True), name="webui")
