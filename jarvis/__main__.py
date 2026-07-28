"""Entry point: `python -m jarvis [--text | --voice | --check | --selftest]`.

Brain: your ChatGPT account via the web app — no API key (sign in once with --chatgpt-login).
Voice additionally needs Deepgram, ElevenLabs, and Picovoice keys (see `.env.example`).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil

from .agent.factory import make_agent
from .config import CONFIG
from .memory.vault import ensure_vault

BANNER = r"""
   __  ____ _____   ___  _____ ___
  / / / _  |  _  \ / / |/ /_ _/ __|
 / /_/ / | | |/ /  V /| |\  \| \__ \
 \____/  |_|_|\_\ \_/ |_|/___/|___/   brain: ChatGPT · research: Perplexity · code: Gemini
"""


async def _confirm(description: str) -> bool:
    print(f"\n  ⚠  Jarvis wants to {description}")
    try:
        answer = await asyncio.to_thread(input, "  Allow it? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in {"y", "yes"}


def _on_tool(name: str, detail: str) -> None:
    print(f"  … {name}: {detail}")


# --------------------------------------------------------------------------- text
async def _run_text_repl() -> None:
    CONFIG.require_claude_cli()
    created = ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    print(BANNER, "· text mode\n")
    if created:
        print(f"Created a fresh memory vault at {CONFIG.vault_path}")
    print(f"Jarvis online. How can I help, {CONFIG.user_name}?  (type 'exit' or Ctrl-D to quit)\n")
    async with make_agent(CONFIG, mode="text", confirm_fn=_confirm, on_tool=_on_tool) as agent:
        while True:
            try:
                user = (await asyncio.to_thread(input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                return
            if not user:
                continue
            if user.lower() in {"exit", "quit", "bye"}:
                print("Goodbye.")
                return
            try:
                reply = await agent.send(user)
            except Exception as exc:  # noqa: BLE001 - keep the REPL alive on errors
                print(f"  [error] {exc}\n")
                continue
            print(f"\njarvis> {reply}\n")


# --------------------------------------------------------------------------- voice
def _voice_event(kind: str, text: str = "") -> None:
    labels = {
        "calibrating": "  (calibrating mic to ambient noise…)",
        "ready": f"Listening for the wake word — {text}",
        "wake": "  (woke)",
        "heard": f"\nyou (voice)> {text}",
        "reply": f"jarvis> {text}",
        "barge_in": "  (interrupted)",
        "loading": f"  {text}",
        "timing": f"  ⏱  {text}",
        "phone": f"  📱 {text}",
        "sleep": "  (back to sleep — say the wake word again)\n",
    }
    print(labels.get(kind, f"  {kind} {text}"))
    _push_to_hud(kind, text)


_hud_retry_after = [0.0]  # unix time to retry after a failure (30s backoff when no HUD is up)


def _push_to_hud(kind: str, text: str = "") -> None:
    """Best-effort: stream voice/phone events to the overlay HUD (the --web server's /emit)."""
    import time as _t

    if _t.time() < _hud_retry_after[0]:
        return
    try:
        import os

        import httpx

        port = os.environ.get("JARVIS_WEB_PORT", "8770")
        httpx.post(f"http://127.0.0.1:{port}/emit", json={"kind": kind, "text": text}, timeout=0.3)
        _hud_retry_after[0] = 0.0
    except Exception:  # noqa: BLE001 - no HUD; back off so we don't stall every event
        _hud_retry_after[0] = _t.time() + 30


async def _run_voice() -> None:
    CONFIG.require_claude_cli()
    backend = CONFIG.resolved_voice_backend()
    if backend == "cloud":
        missing = CONFIG.missing_voice_keys()
        if missing:
            print(
                "Cloud voice needs these in .env (or set JARVIS_VOICE_BACKEND=local):\n  - "
                + "\n  - ".join(missing)
            )
            return
    try:
        from .audio.voice_session import VoiceSession
    except Exception as exc:  # noqa: BLE001 - audio deps / system libs may be missing
        print(f"Audio dependencies aren't ready: {exc}")
        print("  pip install pvporcupine pvrecorder sounddevice numpy httpx")
        print("  and the system PortAudio lib, e.g.:  sudo apt install libportaudio2")
        return

    created = ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    print(BANNER, f"· voice mode ({backend})\n")
    if created:
        print(f"Created a fresh memory vault at {CONFIG.vault_path}")

    # Single-session sharing: if the --web brain is up, the voice loop talks to IT (one ChatGPT
    # browser shared by voice + HUD) instead of opening a second one. Falls back to a local brain
    # if no web server is running. Disable with JARVIS_VOICE_SHARE_WEB=0.
    from .agent.remote import RemoteAgent, web_reachable

    share = os.environ.get("JARVIS_VOICE_SHARE_WEB", "1").strip().lower() not in ("0", "false", "no")

    # The --web brain boots its browser (~15s); wait briefly so we attach to it instead of racing
    # ahead and opening a second ChatGPT session. Falls back to a local brain if it never appears.
    if share and not web_reachable():
        try:
            wait_s = int(os.environ.get("JARVIS_VOICE_WAIT_WEB_S", "45"))
        except ValueError:
            wait_s = 45
        print(f"voice: waiting up to {wait_s}s for the --web brain to come up…")
        for _ in range(wait_s):
            if web_reachable():
                break
            await asyncio.sleep(1)

    def _make_voice_agent():
        if share and web_reachable():
            print("voice: using the shared --web brain (single ChatGPT session)")
            return RemoteAgent(mode="voice")
        print("voice: no --web brain found — starting a local brain for voice.")
        return make_agent(CONFIG, mode="voice", confirm_fn=_confirm, on_tool=_on_tool)

    # Auto-recover: if the transport dies mid-turn, rebuild the agent and keep listening instead of
    # exiting. Ctrl-C still stops cleanly.
    attempt = 0
    while True:
        try:
            async with _make_voice_agent() as agent:
                session = VoiceSession(CONFIG, on_event=_voice_event)
                await session.run(agent)
            return
        except KeyboardInterrupt:
            print("\nGoodbye.")
            return
        except Exception as exc:  # noqa: BLE001 - recover from transport/model failures
            attempt += 1
            wait = min(30, 3 * attempt)
            print(f"\n[recovered from: {type(exc).__name__}: {exc}]")
            print(f"Restarting voice in {wait}s… (say the wake word again once it's up)")
            await asyncio.sleep(wait)


# --------------------------------------------------------------------------- diagnostics
def _preflight() -> None:
    print("Jarvis preflight\n")
    print(f"brain: {CONFIG.brain}", end="")
    if CONFIG.brain == "chatgpt":
        from .integrations.chatgpt import PROFILE
        ready = PROFILE.exists() and any(PROFILE.iterdir())
        print(f"  ({'signed in' if ready else 'run --chatgpt-login'})")
    else:
        print()
    print("\naudio dependencies:")
    for module in ["pvrecorder", "numpy", "httpx", "sounddevice"]:
        try:
            __import__(module)
            print(f"  {module}: ok")
        except Exception as exc:  # noqa: BLE001
            print(f"  {module}: MISSING ({exc})")

    backend = CONFIG.resolved_voice_backend()
    print(f"\nvoice backend: {backend}")
    if backend == "local":
        for module in ["faster_whisper", "piper", "openwakeword"]:
            try:
                __import__(module)
                print(f"  {module}: ok")
            except Exception as exc:  # noqa: BLE001
                print(f"  {module}: MISSING ({exc})")
        from pathlib import Path as _Path

        voice = _Path(CONFIG.piper_model)
        print(f"  piper voice: {'ok' if voice.exists() else 'MISSING — ' + str(voice)}")
        print(f"  whisper model: {CONFIG.whisper_model} (auto-downloads on first use)")
    else:
        missing = CONFIG.missing_voice_keys()
        print("  keys:", "all set" if not missing else "missing —")
        for item in missing:
            print("   -", item)

    print("\ninput devices:")
    try:
        from .audio.mic import Microphone

        for i, dev in enumerate(Microphone.list_devices()):
            print(f"  [{i}] {dev}")
    except Exception as exc:  # noqa: BLE001
        print("  (couldn't list — is a mic connected?)", exc)

    print("\noutput devices:")
    try:
        import sounddevice as sd

        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_output_channels", 0) > 0:
                print(f"  [{i}] {dev['name']}")
    except Exception as exc:  # noqa: BLE001
        print("  (couldn't list — is PortAudio installed?)", exc)


def _voice_selftest() -> None:
    backend = CONFIG.resolved_voice_backend()
    if backend == "cloud" and CONFIG.missing_voice_keys():
        print("Cloud voice keys not set (see --check), or set JARVIS_VOICE_BACKEND=local.")
        return
    from .audio import vad
    from .audio.mic import Microphone

    def speak(text: str) -> None:
        if backend == "local":
            from .audio import local_tts

            local_tts.speak(text, CONFIG.piper_model, CONFIG.audio_output_device)
        else:
            from .audio import tts

            tts.speak(
                text, CONFIG.elevenlabs_api_key, CONFIG.tts_voice_id, CONFIG.tts_model_id,
                16000, CONFIG.audio_output_device,
            )

    def transcribe(pcm: bytes) -> str:
        if backend == "local":
            from .audio import local_stt

            return local_stt.transcribe(pcm, 16000, CONFIG.whisper_model)
        from .audio import stt

        return stt.transcribe(pcm, CONFIG.deepgram_api_key, 16000, CONFIG.stt_model)

    print(f"[{backend}] Speaking a test line (you should hear Jarvis)…")
    speak("Voice self test. Please say a sentence now.")
    print("Listening for up to ~8s…")
    device = CONFIG.audio_input_device if CONFIG.audio_input_device >= 0 else -1
    mic = Microphone(1280, device_index=device)
    mic.start()
    try:
        threshold = CONFIG.vad_threshold or vad.calibrate_threshold(mic.read)
        pcm = vad.record_utterance(
            mic.read, sample_rate=16000, frame_length=1280, threshold=threshold,
            silence_ms=CONFIG.silence_ms, max_s=8, wait_s=6,
        )
    finally:
        mic.stop()
        mic.delete()
    if not pcm:
        print("Heard nothing — check your mic / input device (see `--check`).")
        return
    import time

    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    duration = len(pcm) / 2 / 16000
    peak = int(np.abs(samples).max()) if samples.size else 0
    level = int(np.sqrt(np.mean(samples * samples))) if samples.size else 0
    t0 = time.monotonic()
    text = transcribe(pcm)
    print(f"captured {duration:.1f}s | mic level: peak={peak}, rms={level} (speech is usually rms 400+)")
    print(f"transcribed in {time.monotonic() - t0:.1f}s: {text!r}")
    print("If rms is low (<300), raise your mic volume in system sound settings, or set JARVIS_VAD_THRESHOLD low.")


# --------------------------------------------------------------------------- routines / tasks
def _make_deliver(config):
    from .jobs.notify import notify

    can_speak = not config.missing_voice_keys()

    def deliver(title: str, text: str) -> None:
        notify(title, text)
        if can_speak:
            try:
                from .audio import tts

                tts.speak(
                    text, config.elevenlabs_api_key, config.tts_voice_id,
                    config.tts_model_id, 16000, config.audio_output_device,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  (couldn't speak: {exc})")

    return deliver


async def _run_daemon() -> None:
    CONFIG.require_claude_cli()
    ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    from .routines.scheduler import RoutineManager

    manager = RoutineManager(CONFIG, _make_deliver(CONFIG))
    if CONFIG.enable_brief:
        hour, minute = CONFIG.brief_hm()
        manager.add_morning_brief(hour, minute)
    manager.add_reminders_checker()          # fire persistent reminders
    manager.add_battery_monitor()            # local low-battery warnings
    manager.add_resource_monitor()           # hot CPU / low-memory warnings
    manager.add_daily_rollup(23, 30)         # nightly journal digest
    from .routines.monitors import DEFAULT_MONITORS

    for mon in DEFAULT_MONITORS:             # proactive condition monitors (e.g. important email)
        manager.add_monitor(mon)
    manager.start()
    routines = ", ".join(j.id for j in manager.jobs()) or "none"
    print(BANNER, "· daemon\n")
    print(f"Routines: {routines}. Morning brief at {CONFIG.brief_time}. Ctrl-C to stop.")
    while True:
        await asyncio.sleep(3600)


async def _run_brief() -> None:
    CONFIG.require_claude_cli()
    ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    from .routines.morning_brief import morning_brief

    print("Preparing your briefing…\n")
    print("=== Morning briefing ===\n" + await morning_brief(CONFIG))


async def _run_task(description: str) -> None:
    CONFIG.require_claude_cli()
    ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    from .jobs.runner import JobRunner

    runner = JobRunner(CONFIG)
    job_id = runner.dispatch(description)
    print(f"Dispatched {job_id}. Working…")
    while True:
        await asyncio.sleep(5)
        job = runner.jobs[job_id]
        if job.status in ("done", "error", "rate_limited"):
            break
    print(f"\n[{job.status}]\n{job.result}")


async def _run_meeting() -> None:
    CONFIG.require_claude_cli()
    ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    from .audio.meeting import run_meeting_mode
    
    print(BANNER, "· meeting mode\n")
    async with make_agent(CONFIG, mode="text", confirm_fn=_confirm, on_tool=_on_tool) as agent:
        await run_meeting_mode(CONFIG, agent)


def _run_index() -> None:
    from .memory.embeddings import available
    from .memory.index import VaultIndex

    if not available():
        print("Ollama isn't running. Install https://ollama.com and run:")
        print("  ollama pull nomic-embed-text")
        print("…then retry. (Semantic search is optional; keyword recall works without it.)")
        return
    print("Building semantic index over the vault…")
    stats = VaultIndex(CONFIG.vault_path).build(
        progress=lambda path, n: print(f"  {n} chunks indexed ({path})", end="\r")
    )
    print(f"\nDone: {stats}")


async def _run_telegram() -> None:
    CONFIG.require_claude_cli()
    if not CONFIG.telegram_bot_token:
        print("Set TELEGRAM_BOT_TOKEN in .env (create a bot with @BotFather).")
        return
    ensure_vault(CONFIG.vault_path, CONFIG.user_name)
    from .bridges.telegram import TelegramBridge

    print(BANNER, "· telegram bridge\n")
    print("Message your bot to talk to Jarvis. Ctrl-C to stop.")
    async with make_agent(CONFIG, mode="text", confirm_fn=None, on_tool=lambda n, d: None) as agent:
        await TelegramBridge(CONFIG).run(agent)


async def _run_phone_test() -> None:
    from .integrations.phone.kdeconnect import KDEConnect

    try:
        kc = await KDEConnect(CONFIG.kde_device_id or None).connect()
    except Exception as exc:  # noqa: BLE001
        print("Couldn't connect to KDE Connect:", exc)
        print("Check that `kdeconnect-cli -l` shows your phone paired + reachable.")
        return
    print(f"Connected to device: {kc.device_id}")
    active = await kc.active_notifications()
    print(f"\nActive notifications ({len(active)}):")
    for n in active:
        print(f"  [{n['app']}] {n['title']}: {n['text']}  (repliable={n['repliable']})")
    print("\nWatching for new notifications + calls for 90s.")
    print("→ Send yourself a WhatsApp or Instagram message now…\n")

    async def on_notif(n):
        print(f"  📩 [{n['app']}] {n['title']}: {n['text']}  (repliable={n['repliable']}, id={n['id']})")

    kc.watch_notifications(on_notif)
    kc.watch_calls(lambda c: print(f"  📞 call: {c}"))
    await asyncio.sleep(90)
    print("\n(done watching)")


def _run_web() -> None:
    import os

    import uvicorn

    host = os.environ.get("JARVIS_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("JARVIS_WEB_PORT", "8770"))
    print(BANNER, "· web HUD")
    print(f"\n  Open  http://{host}:{port}  in your browser (Chrome/Edge for voice).\n")
    uvicorn.run("jarvis.webserver:app", host=host, port=port, log_level="warning")


def _run_google_auth() -> None:
    from .integrations.google.auth import run_oauth_flow

    try:
        run_oauth_flow(CONFIG)
        print(f"Google connected. Token saved to {CONFIG.google_token_file}")
    except SystemExit as exc:
        print(exc)
    except Exception as exc:  # noqa: BLE001
        print("Google auth failed:", exc)


def _screen_test() -> None:
    """Diagnose why screen capture works or not, on this exact session. Prints per-method results."""
    import os
    import subprocess
    import time
    from pathlib import Path

    from .vision import screenshot

    print("── screen capture diagnostic ─────────────────────────────")
    print(f"session : {os.environ.get('XDG_SESSION_TYPE', '?')}  "
          f"desktop={os.environ.get('XDG_CURRENT_DESKTOP', '?')}  "
          f"wayland={os.environ.get('WAYLAND_DISPLAY', '-')}  x11={os.environ.get('DISPLAY', '-')}")
    ld = os.environ.get("LD_LIBRARY_PATH", "")
    print(f"LD_LIBRARY_PATH: {'(set → ' + ld[:40] + '…)' if ld else '(clean)'}")

    def blank(p: Path) -> str:
        try:
            from PIL import Image
            with Image.open(p) as im:
                ex = im.convert("RGB").getextrema()
            return "BLANK/black" if all(lo == hi for lo, hi in ex) else "has content ✓"
        except Exception as exc:  # noqa: BLE001
            return f"(couldn't inspect: {exc})"

    candidates = {
        "grim": ["grim"],
        "gnome-screenshot": ["gnome-screenshot", "-f"],
        "spectacle": ["spectacle", "-b", "-n", "-o"],
        "scrot": ["scrot", "-o"],
        "import": ["import", "-window", "root"],
    }
    import shutil

    env = screenshot._clean_env()
    any_ok = False
    for name, argv in candidates.items():
        if not shutil.which(name):
            continue
        out = f"/tmp/jarvis-screentest-{name}-{int(time.time())}.png"
        try:
            r = subprocess.run(argv + [out], timeout=15, capture_output=True, env=env)
            size = Path(out).stat().st_size if Path(out).exists() else 0
            verdict = blank(Path(out)) if size else "no file"
            if size and "content" in verdict:
                any_ok = True
            print(f"\n[{name}] exit={r.returncode} size={size}B → {verdict}")
            if r.stderr:
                print(f"   stderr: {r.stderr.decode(errors='ignore').strip()[:200]}")
            if size and "content" in verdict:
                print(f"   ✓ saved {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[{name}] FAILED: {exc}")

    if os.environ.get("WAYLAND_DISPLAY"):
        print("\n── XDG portal (direct, with errors) ──")
        import asyncio
        import traceback

        try:
            out = f"/tmp/jarvis-portal-{int(time.time())}.png"
            path = asyncio.run(screenshot._portal_async(out))
            print(f"portal returned: {path}  [{blank(Path(path)) if path and Path(path).exists() else 'no file'}]")
        except Exception:  # noqa: BLE001
            print("portal RAISED:")
            traceback.print_exc()

    print("\n── result via jarvis capture() ──")
    if os.environ.get("WAYLAND_DISPLAY"):
        print("(Wayland → using the XDG desktop portal. GNOME may pop a one-time 'Share' dialog — accept it.)")
    p = screenshot.capture()
    verdict_p = blank(Path(p)) if p and Path(p).exists() else "no file"
    print(f"capture() → {p or 'None'}  [{verdict_p if p else 'failed/blank'}]")
    ok = bool(p) and "content" in verdict_p
    print("\n" + ("✅ Screen capture works — Jarvis can see your screen." if ok else
                  "❌ Still failed. If no permission dialog appeared, the portal backend may be missing:\n"
                  "   install it with:  sudo apt install xdg-desktop-portal-gnome\n"
                  "   then re-run this test."))


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Jarvis — voice-first AI assistant")
    parser.add_argument("--text", action="store_true", help="terminal chat (default)")
    parser.add_argument("--voice", action="store_true", help="voice mode (wake word + speech)")
    parser.add_argument("--daemon", action="store_true", help="run proactive routines (morning brief, monitors)")
    parser.add_argument("--brief", action="store_true", help="run the morning briefing once, now")
    parser.add_argument("--task", metavar="DESC", help="dispatch one background task and wait for the result")
    parser.add_argument("--telegram", action="store_true", help="remote access via a Telegram bot")
    parser.add_argument("--meeting", action="store_true", help="capture a meeting and summarize it")
    parser.add_argument("--google-auth", action="store_true", help="one-time Google (Calendar/Gmail/Tasks) auth")
    parser.add_argument("--phone-test", action="store_true", help="test the KDE Connect phone bridge")
    parser.add_argument("--web", action="store_true", help="launch the WebGL Jarvis HUD in your browser")
    parser.add_argument("--index", action="store_true", help="build the semantic memory index (needs Ollama)")
    parser.add_argument("--check", action="store_true", help="preflight: deps, keys, audio devices")
    parser.add_argument("--selftest", action="store_true", help="one live TTS→mic→STT round trip")
    parser.add_argument("--screen-test", action="store_true", help="diagnose screen capture (Wayland/X11)")
    parser.add_argument("--perplexity-login", action="store_true",
                        help="one-time: sign in to Perplexity so Jarvis can deep-research as your account")
    parser.add_argument("--chatgpt-login", action="store_true",
                        help="one-time: sign in to ChatGPT so Jarvis can think through your account")
    args = parser.parse_args()

    try:
        if args.chatgpt_login:
            from .integrations import chatgpt
            chatgpt.login()
        elif args.perplexity_login:
            from .integrations import research
            research.login()
        elif args.check:
            _preflight()
        elif args.screen_test:
            _screen_test()
        elif args.google_auth:
            _run_google_auth()
        elif args.phone_test:
            asyncio.run(_run_phone_test())
        elif args.web:
            _run_web()
        elif args.index:
            _run_index()
        elif args.selftest:
            _voice_selftest()
        elif args.task:
            asyncio.run(_run_task(args.task))
        elif args.brief:
            asyncio.run(_run_brief())
        elif args.daemon:
            asyncio.run(_run_daemon())
        elif args.telegram:
            asyncio.run(_run_telegram())
        elif args.meeting:
            asyncio.run(_run_meeting())
        elif args.voice:
            asyncio.run(_run_voice())
        else:
            asyncio.run(_run_text_repl())
    except KeyboardInterrupt:
        print("\nGoodbye.")


if __name__ == "__main__":
    main()
