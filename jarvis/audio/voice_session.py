"""The voice state machine: wake -> listen -> think -> speak -> follow-up.

  IDLE ──"Jarvis"──▶ LISTEN ──(silence)──▶ THINK ──▶ SPEAK ──▶ follow-up window ──▶ IDLE

Follow-ups: after speaking, it listens briefly so you can continue without repeating the wake
word. Barge-in: while speaking, a monitor thread watches the mic and cuts playback if you talk
over it (heuristic; may false-trigger from speaker echo without AEC — disable via JARVIS_BARGE_IN=0).
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..config import Config
from . import stt, tts, vad
from .mic import Microphone
from .wake import WakeWord

EventCallback = Callable[..., None]

POST_WAKE_WAIT_S = 4.0  # how long to wait for you to start speaking after the wake word

# KDE Connect announces these (WhatsApp comes from the Baileys bridge instead — see _watch_whatsapp)
_MESSAGING_APPS = ("instagram", "messenger", "telegram", "signal", "messages", "sms")


def _is_messaging_app(app: str) -> bool:
    a = (app or "").lower()
    return any(m in a for m in _MESSAGING_APPS)


class VoiceSession:
    def __init__(self, config: Config, on_event: Optional[EventCallback] = None) -> None:
        self.config = config
        self.on_event: EventCallback = on_event or (lambda kind, text="": None)
        self.backend = config.resolved_voice_backend()
        if self.backend == "local":
            from .local_wake import LocalWakeWord

            self.wake = LocalWakeWord(threshold=config.wake_threshold)
        else:
            self.wake = WakeWord(config.picovoice_access_key, config.wake_keyword)
        device = config.audio_input_device if config.audio_input_device >= 0 else -1
        self.mic = Microphone(self.wake.frame_length, device_index=device)
        self.sample_rate = self.wake.sample_rate
        self.frame_length = self.wake.frame_length
        self.threshold = float(config.vad_threshold)

    # --- stages ----------------------------------------------------------
    async def _wait_for_wake_or_event(self):
        """Block until the wake word fires or a phone event is queued. Returns ('wake'|'event', payload)."""
        while True:
            if not self._events.empty():
                return ("event", self._events.get_nowait())
            frame = await asyncio.to_thread(self.mic.read)  # frees the loop for D-Bus signals
            if self.wake.process(frame):
                return ("wake", None)

    async def _watch_whatsapp(self) -> None:
        """Poll the WhatsApp bridge and announce new direct messages (skips groups/newsletters)."""
        from ..integrations import whatsapp

        seen = set()
        try:
            for m in whatsapp.inbox():
                seen.add(f"{m.get('from')}|{m.get('ts')}")  # seed: don't announce the backlog
        except Exception:  # noqa: BLE001
            pass
        while True:
            await asyncio.sleep(4)
            try:
                for m in whatsapp.inbox():
                    key = f"{m.get('from')}|{m.get('ts')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    frm = str(m.get("from", ""))
                    if "@newsletter" in frm or "@g.us" in frm:
                        continue
                    sender, text = m.get("name", "someone"), m.get("text", "")
                    handled = await self._away_converse(frm, sender, text)
                    await self._events.put({
                        "type": "message", "app": "WhatsApp",
                        "title": sender, "text": text, "id": frm,
                        "repliable": True, "handled": handled,
                    })
            except Exception:  # noqa: BLE001
                pass

    def _ensure_whatsapp_bridge(self) -> None:
        """Check the bridge is up. We DON'T start it here — the jarvis-whatsapp systemd service owns
        it. Starting a second instance desyncs the WhatsApp encryption session (Bad MAC), so we only
        nudge the service to (re)start if it's registered, and otherwise just warn."""
        import subprocess

        from ..integrations import whatsapp

        try:
            if whatsapp.status():
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            has_service = subprocess.run(
                ["systemctl", "--user", "list-unit-files", "jarvis-whatsapp.service"],
                capture_output=True, text=True, timeout=5,
            ).stdout.find("jarvis-whatsapp") >= 0
            if has_service:
                subprocess.run(["systemctl", "--user", "start", "jarvis-whatsapp"], timeout=8)
                self.on_event("phone", "starting WhatsApp bridge service…")
            else:
                self.on_event("phone", "WhatsApp bridge is down — run scripts/install-whatsapp-service.sh")
        except Exception:  # noqa: BLE001
            pass

    async def _away_converse(self, jid: str, sender: str, text: str) -> bool:
        """While away, carry on a real conversation with the sender. Returns True if handled."""
        from ..agent import away

        if not away.is_away():
            return False
        reply = await away.respond(self.config, jid, sender, text)
        if not reply:
            return False
        try:
            from ..integrations import whatsapp

            whatsapp.send(jid, reply)
            self.on_event("phone", f"away: replied to {sender} — “{reply[:60]}”")
        except Exception:  # noqa: BLE001
            return False
        return True

    async def _setup_phone(self) -> None:
        self._ensure_whatsapp_bridge()               # revive the bridge if it died
        asyncio.create_task(self._watch_whatsapp())  # WhatsApp alerts via the bridge
        self._kc = None
        try:
            from ..integrations.phone.kdeconnect import KDEConnect

            self._kc = await KDEConnect(self.config.kde_device_id or None).connect()
        except Exception:  # noqa: BLE001 - KDE Connect (Instagram/SMS/calls) is optional
            self._kc = None
            return

        async def on_notif(note: dict) -> None:
            if _is_messaging_app(note.get("app", "")):
                await self._events.put({"type": "message", **note})

        self._kc.watch_notifications(on_notif)
        try:
            self._kc.watch_calls(lambda c: self._events.put_nowait({"type": "call", **c}))
        except Exception:  # noqa: BLE001
            pass
        self.on_event("phone", "phone connected — I'll speak up on messages and calls")

    async def _handle_phone_event(self, event: dict, agent) -> None:
        from ..agent import away

        if event.get("type") == "call":
            who = event.get("name") or event.get("number") or "an unknown number"
            number = event.get("number")
            if "missed" in (event.get("event") or "").lower():
                self._speak(f"You missed a call from {who}.")
            elif away.is_away() and number:
                # Auto-attendant: text the caller that the user is unavailable.
                try:
                    if self._kc is not None:
                        self._kc.send_sms(number, away.oneliner(self.config))
                except Exception:  # noqa: BLE001
                    pass
                self._speak(f"{who} is calling. You're away, so I texted them you're unavailable.")
            else:
                self._speak(f"Incoming call from {who}.")
            return

        app = event.get("app", "phone")
        who = event.get("title", "someone")
        if "@" in str(who) or str(who).replace("+", "").isdigit():
            who = "someone"  # never speak a raw JID/number as the sender name
        msg = event.get("text", "")
        self.on_event("phone", f"{app} from {who}: {msg}")
        # Auto-attendant for repliable notifications (Instagram/SMS), unless already handled (WhatsApp).
        if away.is_away() and event.get("repliable") and event.get("id") and not event.get("handled"):
            try:
                reply = await away.respond(self.config, str(event.get("id")), who, msg)
                if self._kc is not None and reply:
                    await self._kc.reply(str(event.get("id")), reply)
            except Exception:  # noqa: BLE001
                pass
        # Announce only — do NOT open a listening window. Otherwise anything the user happens
        # to say afterwards gets captured as a "reply". To respond, the user says the wake word
        # and asks explicitly ("Jarvis, reply to {who} that …"); the brain then uses whatsapp_inbox
        # to find this message and whatsapp_send / phone_reply to answer.
        self._last_message = {
            "app": app, "who": who, "text": msg,
            "id": event.get("id"), "repliable": event.get("repliable"),
        }
        if not away.is_away():  # while away Jarvis handles it silently; don't talk to an empty room
            self._speak(f"{app} message from {who}. {msg}.")

    def _record_transcript(self, wait_s: float) -> Optional[str]:
        pcm = vad.record_utterance(
            self.mic.read,
            sample_rate=self.sample_rate,
            frame_length=self.frame_length,
            threshold=self.threshold,
            silence_ms=self.config.silence_ms,
            max_s=self.config.max_utterance_s,
            wait_s=wait_s,
        )
        if not pcm:
            # nothing above the VAD threshold → mic muted/wrong device, or threshold too high
            self.on_event("timing", f"no audio captured (VAD threshold={self.threshold})")
            return None
        duration = len(pcm) / 2 / self.sample_rate
        start = time.monotonic()
        text = self._transcribe(pcm)
        self.on_event("timing",
                      f"heard {duration:.1f}s clip, transcribed in {time.monotonic() - start:.1f}s "
                      f"-> {'\"'+text+'\"' if text else 'EMPTY (STT found no words)'}")
        return text

    def _transcribe(self, pcm: bytes) -> str:
        if self.backend == "local":
            from . import local_stt

            return local_stt.transcribe(
                pcm, self.sample_rate, self.config.whisper_model, self.config.whisper_beam
            )
        return stt.transcribe(pcm, self.config.deepgram_api_key, self.sample_rate, self.config.stt_model)

    def _barge_in_monitor(self, stop: threading.Event) -> None:
        # No hardware echo-cancellation, so his own speaker leaks into the mic. Sample that echo
        # level for the first ~0.6s of playback, then set the interrupt bar ABOVE it — so only the
        # user's voice (louder than the echo) cuts him off, not his own speech.
        echo_samples: list[float] = []
        t0 = time.monotonic()
        while not stop.is_set() and time.monotonic() - t0 < 0.6:
            try:
                frame = self.mic.read()
            except Exception:  # noqa: BLE001
                return
            echo_samples.append(vad.rms(frame))
        echo = 0.0
        if echo_samples:
            echo_samples.sort()
            echo = echo_samples[int(len(echo_samples) * 0.75)]  # 75th-pct echo level
        trigger = max(self.threshold * 2.5, echo * 1.9)

        needed, run = 8, 0  # ~0.6s of sustained speech above the echo before cutting
        while not stop.is_set():
            try:
                frame = self.mic.read()
            except Exception:  # noqa: BLE001
                return
            if vad.rms(frame) >= trigger:
                run += 1
                if run >= needed:
                    self.on_event("barge_in")
                    stop.set()
                    return
            else:
                run = 0

    # abbreviations/symbols Piper otherwise reads awkwardly → spoken forms
    _SPEECH_SUBS = [
        (r"\be\.g\.\s*", "for example, "),
        (r"\bi\.e\.\s*", "that is, "),
        (r"\betc\.?", "and so on"),
        (r"\bvs\.?\b", "versus"),
        (r"\bapprox\.?\b", "approximately"),
        (r"\bDr\.\s*", "Doctor "),
        (r"\bMr\.\s*", "Mister "),
        (r"\bMrs\.\s*", "Missus "),
        (r"\bMs\.\s*", "Miss "),
        (r"\bSt\.\s*", "Saint "),
        (r"\ba\.m\.", "AM"), (r"\bp\.m\.", "PM"),
    ]
    # currency: symbol before a number → "<number> <unit>"
    _CURRENCY = [(r"\$(\d[\d,.]*)", r"\1 dollars"), (r"£(\d[\d,.]*)", r"\1 pounds"),
                 (r"€(\d[\d,.]*)", r"\1 euros"), (r"₹(\d[\d,.]*)", r"\1 rupees")]
    _SYMBOL_SUBS = [
        ("&", " and "), ("%", " percent"), ("°", " degrees"), ("=", " equals "),
        ("+", " plus "), ("~", " about "), ("×", " times "), ("@", " at "),
    ]

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """Normalise text so the TTS pronounces it naturally: strip markdown/emoji and expand
        common abbreviations and symbols into spoken words."""
        import re

        t = text
        t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)   # code fences
        t = re.sub(r"`([^`]*)`", r"\1", t)                   # inline code
        t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)     # links/images -> label
        t = re.sub(r"https?://\S+", "", t)                   # bare URLs — don't read them out
        t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.MULTILINE)  # headings
        t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)       # bullet markers
        t = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", t)  # **bold** / *italic* / _em_
        t = t.replace("*", " ").replace("#", " ").replace("`", " ")  # any stray marks

        for pat, rep in VoiceSession._SPEECH_SUBS:
            t = re.sub(pat, rep, t, flags=re.IGNORECASE)
        for pat, rep in VoiceSession._CURRENCY:
            t = re.sub(pat, rep, t)
        for sym, rep in VoiceSession._SYMBOL_SUBS:
            t = t.replace(sym, rep)

        # strip emoji / pictographs so they aren't announced by name
        t = re.sub(
            "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF\U00002190-\U000021FF️]",
            "", t,
        )
        # newlines become sentence pauses; collapse whitespace
        t = re.sub(r"\s*\n+\s*", ". ", t)
        t = re.sub(r"\.\s*\.\s*", ". ", t)   # avoid double periods
        t = re.sub(r"[ \t]{2,}", " ", t)
        return t.strip()

    def _speak(self, text: str) -> None:
        text = self._clean_for_speech(text)
        stop = threading.Event()
        monitor: Optional[threading.Thread] = None
        if self.config.enable_barge_in:
            monitor = threading.Thread(target=self._barge_in_monitor, args=(stop,), daemon=True)
            monitor.start()
        try:
            if self.backend == "local":
                from . import local_tts

                local_tts.speak(
                    text, self.config.piper_model, self.config.audio_output_device, stop_event=stop
                )
            else:
                tts.speak(
                    text,
                    self.config.elevenlabs_api_key,
                    self.config.tts_voice_id,
                    self.config.tts_model_id,
                    self.sample_rate,
                    self.config.audio_output_device,
                    stop_event=stop,
                )
        finally:
            stop.set()
            if monitor is not None:
                monitor.join(timeout=1.0)

    async def _watch_screen(self) -> None:
        """While live screen-share is on, notice big changes and speak up only on a real problem."""
        from ..agent.autonomous import run_once
        from ..vision import live, screenshot
        from ..routines.monitors import parse_alert

        last: Optional[list] = None
        while True:
            await asyncio.sleep(20)
            if not live.is_active():
                last = None
                continue
            path = await asyncio.to_thread(screenshot.capture)
            if not path:
                continue
            try:
                from PIL import Image

                with Image.open(path) as im:
                    sig = list(im.convert("L").resize((32, 32)).getdata())
            except Exception:  # noqa: BLE001
                continue
            if last is None:
                last = sig
                continue
            diff = sum(abs(a - b) for a, b in zip(sig, last)) / len(sig)
            last = sig
            if diff < 12:  # screen barely changed — nothing to look at
                continue
            out = await run_once(
                self.config,
                f"A current screenshot of {self.config.user_name}'s screen is at {path}. Read it. If "
                "there is a clear problem they'd want flagged right now (an error message, a failed "
                "build or test, a crash or warning dialog), reply 'ALERT: <one short sentence>'. "
                "Otherwise reply exactly 'NONE'.",
                system="You are Jarvis quietly watching {}'s screen. Only speak up for real problems.".format(
                    self.config.user_name
                ),
                effort="low",
            )
            alert = parse_alert(out)
            if alert:
                self._speak(alert)

    # --- AFK / welcome-back ---------------------------------------------
    @staticmethod
    def _idle_ms() -> Optional[int]:
        """Current input-idle time in ms via GNOME's Mutter IdleMonitor (Wayland-friendly)."""
        import os
        import re
        import subprocess

        env = {k: v for k, v in os.environ.items() if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")}
        try:
            r = subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                 "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                 "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"],
                env=env, capture_output=True, text=True, timeout=5,
            )
            m = re.search(r"(\d+)", r.stdout)
            return int(m.group(1)) if m else None
        except Exception:  # noqa: BLE001
            return None

    def _weather(self) -> Optional[str]:
        if not self.config.city:
            return None
        try:
            import httpx

            r = httpx.get(f"https://wttr.in/{self.config.city}?format=%C+%t", timeout=6)
            if r.status_code == 200 and r.text.strip():
                return r.text.strip()
        except Exception:  # noqa: BLE001
            pass
        return None

    def _welcome_back_brief(self, agent, mins: int) -> str:
        import time as _t

        from ..integrations import system_stats

        name = self.config.user_name
        parts = [f"Welcome back, {name}."]
        if mins:
            parts.append(f"You were away about {mins} minute{'s' if mins != 1 else ''}.")
        parts.append(f"It's {_t.strftime('%I:%M %p').lstrip('0')}.")

        w = self._weather()
        if w:
            parts.append(f"It's {w} outside.")

        try:
            from ..integrations.google import calendar as gcal

            ag = gcal.agenda(self.config, 1)
            if ag and "not connected" not in ag.lower() and "no upcoming" not in ag.lower():
                first = next((ln.strip("-• ") for ln in ag.splitlines() if ln.strip()), "")
                if first:
                    parts.append(f"On your calendar: {first}.")
        except Exception:  # noqa: BLE001
            pass

        try:
            d = system_stats.snapshot()
            g = d.get("gpu")
            line = f"System's fine — CPU {d['cpu_percent']:.0f} percent"
            if d.get("cpu_temp"):
                line += f" at {d['cpu_temp']:.0f} degrees"
            if g:
                line += f", GPU {g['util']} percent"
            line += f", {d['mem']['total_gb'] - d['mem']['used_gb']:.0f} gigs of memory free."
            parts.append(line)
        except Exception:  # noqa: BLE001
            pass

        try:
            rep = agent.job_runner.status_report()
            if rep and "No background tasks" not in rep:
                done = rep.count("[done]")
                running = rep.count("[running]")
                if running:
                    parts.append(f"You have {running} background task{'s' if running != 1 else ''} still running.")
                if done:
                    parts.append(f"{done} background task{'s' if done != 1 else ''} finished while you were away.")
        except Exception:  # noqa: BLE001
            pass

        return " ".join(parts)

    async def _watch_afk(self, agent) -> None:
        """Notice a long idle gap, then greet + brief the user when they come back."""
        threshold_ms = max(1, self.config.afk_minutes) * 60_000
        away = False
        prev_idle = 0
        while True:
            await asyncio.sleep(5)
            idle = await asyncio.to_thread(self._idle_ms)
            if idle is None:
                continue
            if not away and idle >= threshold_ms:
                away = True
            elif away and idle < prev_idle:  # activity reset the idle timer → they're back
                away = False
                mins = int(prev_idle / 60_000)
                brief = await asyncio.to_thread(self._welcome_back_brief, agent, mins)
                self.on_event("reply", brief)
                self._speak(brief)
            prev_idle = idle

    def _augment(self, transcript: str) -> str:
        """Attach live context to a spoken turn: a fresh screenshot (if screen-share is on) and
        the last unanswered phone message (so "reply to him …" resolves without re-listening)."""
        from ..vision import analyze, live, screenshot

        prefix = ""
        if live.is_active():
            path = screenshot.capture()
            if path:
                # The local text brain can't see images, so describe the screen for it each turn.
                desc = analyze.describe(path, "Describe what's on this screen concisely, including any "
                                              "app, text, code, or errors.", self.config)
                if desc:
                    prefix += f"[Live view of {self.config.user_name}'s screen right now: {desc}]\n"
                else:
                    prefix += f"[Live screen-share is ON; screenshot saved at {path}.]\n"
        last = getattr(self, "_last_message", None)
        if last:
            prefix += (
                f"[Most recent incoming {last['app']} message from {last['who']}: \"{last['text']}\". "
                f"If {self.config.user_name} wants to reply, use whatsapp_send with the name '{last['who']}'.]\n"
            )
        return f"{prefix}\n{transcript}" if prefix else transcript

    def _greeting(self) -> str:
        import time as _t

        h = _t.localtime().tm_hour
        part = "morning" if h < 12 else "afternoon" if h < 17 else "evening"
        name = self.config.user_name
        return f"Good {part}, {name}. Jarvis online — say 'Hey Jarvis' whenever you need me."

    # --- main loop -------------------------------------------------------
    async def run(self, agent) -> None:
        """Drive the session. `agent` is a connected JarvisAgent."""
        self.mic.start()
        self._last_message = None
        self._events: asyncio.Queue = asyncio.Queue()
        await self._setup_phone()

        from ..jobs import notify, reminders, timers  # speak timer/reminder alerts aloud in voice mode

        timers.set_announcer(self._speak)
        reminders.set_announcer(self._speak)
        notify.set_announcer(self._speak)
        asyncio.create_task(reminders.run_checker(self.config.vault_path))
        asyncio.create_task(self._watch_screen())  # proactive alerts during live screen-share
        asyncio.create_task(self._watch_afk(agent))  # welcome-back brief after a long idle gap

        if self.config.screen_always:  # keep screen vision on from the start
            from ..vision import live

            live.set_active(True)
        try:
            if self.threshold <= 0:
                self.on_event("calibrating")
                self.threshold = vad.calibrate_threshold(self.mic.read)
            if self.backend == "local":
                from . import local_stt

                self.on_event("loading", "warming up speech model…")
                local_stt.warmup(self.config.whisper_model)
            phrase = "Hey Jarvis" if self.backend == "local" else self.config.wake_keyword
            self.on_event("ready", f"wake word: '{phrase}'")
            self._speak(self._greeting())  # Jarvis speaks first
            while True:
                kind, payload = await self._wait_for_wake_or_event()
                if kind == "event":
                    await self._handle_phone_event(payload, agent)
                    continue
                self.on_event("wake")
                transcript = self._record_transcript(wait_s=POST_WAKE_WAIT_S)
                if not transcript:
                    # Woke but captured nothing intelligible → say so instead of going silent, so it
                    # never looks "stuck". Usually a mic/VAD/threshold issue (see the timing log).
                    self.on_event("heard", "(nothing captured)")
                    self._speak("Sorry sir, I didn't catch that.")
                    self.on_event("sleep")
                    continue
                while transcript:
                    self.on_event("heard", transcript)
                    t_lower = transcript.lower().strip()

                    # Executive Wake Briefing: "wake up jarvis", "wake up", "clap", "status report", "system pulse", "subsystems"
                    if any(phrase in t_lower for phrase in ("wake up", "wake up jarvis", "clapped", "clap", "status report", "subsystems", "system report", "pulse")):
                        from ..integrations import system_stats
                        from ..agent import ai_researcher
                        from .. import hud_state

                        # 1. Hardware stats
                        st = system_stats.snapshot()
                        cpu_p = round(st.get("cpu_percent", 0))
                        mem_total = st.get("mem", {}).get("total_gb", 16)
                        mem_used = st.get("mem", {}).get("used_gb", 8)
                        mem_free = round(mem_total - mem_used, 1)
                        gpu_info = f", GPU load is at {st['gpu']['util']} percent" if st.get("gpu") else ""
                        
                        # 2. Detailed subsystems status (which are online and which are offline)
                        h = hud_state.health()
                        systems = h.get("systems", [])
                        online_names = [s["name"] for s in systems if s.get("ok")]
                        offline_names = [s["name"] for s in systems if not s.get("ok")]

                        if offline_names:
                            subsys_msg = f"Subsystems: {len(online_names)} online including {', '.join(online_names[:4])}. Notice: {', '.join(offline_names)} {'is' if len(offline_names) == 1 else 'are'} currently offline."
                        else:
                            subsys_msg = f"All {len(online_names)} subsystems are online and fully operational: {', '.join(online_names)}."

                        # 3. AI Research Agent status & reports
                        ai_st = ai_researcher.get_agent_status()
                        rep_cnt = ai_st.get("reports_count", 0)
                        last_rep = ai_st.get("last_report")
                        if rep_cnt > 0:
                            ai_msg = f"The 24/7 AI Research Agent is active and has submitted {rep_cnt} breakthrough report{'s' if rep_cnt != 1 else ''} to your Documents folder"
                            if last_rep:
                                ai_msg += f", with the latest on {last_rep}."
                            else:
                                ai_msg += "."
                        else:
                            ai_msg = "The 24/7 AI Research Agent is running in the background, continuously monitoring arXiv and Hugging Face."

                        # 4. Message queues check
                        wa_msg = ""
                        try:
                            from ..integrations import whatsapp
                            unread = [m for m in whatsapp.inbox() if not m.get("read", True)]
                            if unread:
                                wa_msg = f" You have {len(unread)} unread message{'s' if len(unread) != 1 else ''}."
                        except Exception:
                            pass

                        reply = (
                            f"Online and ready, {self.config.user_name}. "
                            f"Computer stats: CPU is at {cpu_p} percent with {mem_free} gigabytes of memory free{gpu_info}. "
                            f"{subsys_msg} "
                            f"{ai_msg}"
                            f"{wa_msg}"
                        )
                        
                        self.on_event("reply", reply)
                        self._speak(reply)
                        break

                    # Instant Phone Command Intercept: "open my phone", "open phone", "mirror phone", "show my phone"
                    if any(phrase in t_lower for phrase in ("open my phone", "open phone", "mirror my phone", "mirror phone", "show my phone", "show phone", "screen my phone")):
                        from ..integrations import apps
                        ok, msg = apps.phone_mirror()
                        reply = "Opening your phone on screen now, sir." if ok else msg
                        self.on_event("reply", reply)
                        self._speak(reply)
                        break

                    # Instant Ring Phone Intercept: "ring my phone", "find my phone", "where is my phone"
                    if any(phrase in t_lower for phrase in ("ring my phone", "find my phone", "where is my phone", "call my phone")):
                        from ..integrations import apps
                        ok = apps.phone_ring(self.config.kde_device_id or None)
                        reply = "Ringing your phone now, sir." if ok else "Couldn't reach your phone over KDE Connect."
                        self.on_event("reply", reply)
                        self._speak(reply)
                        break

                    # Instant Coding / Antigravity Voice Intercept
                    from ..integrations import coding
                    v_ctx = coding.active_context()
                    is_code_explicit = any(
                        phrase in t_lower
                        for phrase in (
                            "code this", "code for me", "code ", "in vs code", "in vscode",
                            "on vs code", "on vscode", "antigravity", "agy", "fix the code",
                            "refactor the code", "open antigravity", "launch antigravity",
                        )
                    )
                    is_vs_active = v_ctx.get("is_vscode_active", False)
                    has_coding_intent = any(
                        t_lower.startswith(verb) or f" {verb}" in t_lower
                        for verb in (
                            "fix ", "implement ", "refactor ", "create ", "add ", "debug ",
                            "write a test", "change the code", "build "
                        )
                    )

                    if is_code_explicit or (is_vs_active and has_coding_intent):
                        target_folder = v_ctx.get("folder")
                        target_file = v_ctx.get("file")
                        target_path = v_ctx.get("file_path")
                        proj_name = v_ctx.get("project_name") or "your project"
                        target_label = target_file or proj_name
                        if not target_folder:
                            reply = "I couldn't detect an active project or file in VS Code for agy, sir."
                        else:
                            runner = getattr(agent, "job_runner", None)
                            if runner is None:
                                if not hasattr(self, "_job_runner"):
                                    from ..jobs.runner import JobRunner
                                    self._job_runner = JobRunner(self.config)
                                runner = self._job_runner

                            job_id = runner.dispatch_antigravity(
                                raw_prompt=transcript,
                                folder=target_folder,
                                active_file=target_file,
                                active_file_path=target_path,
                            )
                            reply = (
                                f"On it, sir. Refining prompt and running agy on {target_label}. "
                                "I'll tell you when it's done."
                            )
                        self.on_event("reply", reply)
                        self._speak(reply)
                        break

                    start = time.monotonic()
                    try:
                        reply = await agent.send(self._augment(transcript))
                    except Exception as exc:  # noqa: BLE001 - never let one bad turn kill the loop
                        reply = "Something went wrong on that one, sir."
                        self.on_event("error", f"brain: {exc}")
                    self.on_event("timing", f"thought in {time.monotonic() - start:.1f}s")
                    if not (reply or "").strip():
                        reply = "I don't have an answer for that, sir."
                    self.on_event("reply", reply)
                    self._speak(reply)
                    # By default require the wake word again for each turn (no auto-listen after a
                    # reply). Set JARVIS_FOLLOWUP=1 to keep the conversational follow-up window.
                    if not self.config.enable_followup:
                        break
                    transcript = self._record_transcript(wait_s=self.config.follow_up_s)
                self.on_event("sleep")
        finally:
            self.mic.stop()
            self.mic.delete()
            self.wake.delete()
