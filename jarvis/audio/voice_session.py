"""The voice state machine: wake -> listen -> think -> speak -> follow-up.

  IDLE ──"Jarvis"──▶ LISTEN ──(silence)──▶ THINK ──▶ SPEAK ──▶ follow-up window ──▶ IDLE

Follow-ups: after speaking, it listens briefly so you can continue without repeating the wake
word. Barge-in: while speaking, a monitor thread watches the mic and cuts playback if you talk
over it (heuristic; may false-trigger from speaker echo without AEC — disable via JARVIS_BARGE_IN=0).
"""

from __future__ import annotations

import asyncio
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..config import Config
from . import endpoint, hotkey, inputs, levels, speech_control, stt, tts, vad
from .conversation import END, IGNORE, ConversationSession, split_closing
from .conversation import State as ConvState
from .mic import Microphone
from .wake import WakeWord

EventCallback = Callable[..., None]

POST_WAKE_WAIT_S = 4.0  # how long to wait for you to start speaking after the wake word
# The wake word is ignored this long after Jarvis stops speaking: the tail of his own voice, still
# in the room, has woken him before. Inside a conversation no wake word is needed anyway.
WAKE_COOLDOWN_S = 1.0
# A request that has produced nothing to say after this long gets a short "give me a moment".
ACK_AFTER_S = 1.4

# KDE Connect announces these (WhatsApp comes from the Baileys bridge instead — see _watch_whatsapp)
_MESSAGING_APPS = ("instagram", "messenger", "telegram", "signal", "messages", "sms")


# Requests the voice loop answers itself, before the brain. Whole-sentence matches only: these
# were substring tests, so "what is impulse" contained "pulse" and got a system status report, and
# "open phonepe" contained "open phone" and mirrored the phone instead of opening PhonePe.
_INSTANT = (
    ("status", re.compile(r"(?:wake up(?: jarvis)?|(?:give me a |run a )?(?:status|system) report|"
                          r"(?:protocol )?system pulse|run diagnostics(?: sequence)?|"
                          r"subsystems?(?: status| check| report)?)")),
    ("mirror_phone", re.compile(r"(?:open|mirror|show|screen)(?: my)? phone(?: screen)?(?: on (?:the )?screen)?")),
    ("ring_phone", re.compile(r"(?:ring|find|call)(?: my)? phone|where(?:'s| is) my phone")),
)


# Asking for the coding agent by name. This used to be substring matching on phrases including
# "code " — and "message 98… with the country code plus 91" contains "code ", so a WhatsApp
# request went to the coding dialogue and came back as "Yes.". Whole words now, and the codes
# that are not source code are named so they can never count.
_CODE_EXPLICIT = re.compile(
    r"(?ix)^code\s+\w|\b(?:code\s+(?:this|that|it|for\s+me|up)|write\s+(?:the\s+|some\s+)?code|"
    r"(?:in|on|open|launch)\s+vs\s?code|vs\s?code|antigravity|agy|"
    r"(?:fix|refactor|change|debug|review)\s+(?:the|this|my)\s+code)\b")
_NOT_SOURCE_CODE = re.compile(
    r"(?i)\b(?:country|area|pin|zip|postal|std|isd|dial(?:ling)?|otp|verification|promo|coupon|"
    r"discount|qr|dress|access|security|login|sms)\s*codes?\b|\bcode\s+word\b")
_CODING_VERB = re.compile(
    r"(?i)^(?:fix|implement|refactor|create|add|debug|build|write\s+a\s+test|change\s+the\s+code)\b")


def wants_coding_agent(transcript: str, editor_in_front: bool = False) -> bool:
    """Is this a request for the coding agent? Never a message request, never a phone code."""
    said = re.sub(r"^(?:hey\s+)?jarvis[,.!:\s]*", "", (transcript or "").strip(), flags=re.I)
    from ..message_command import looks_like_message
    if looks_like_message(said):
        return False
    probe = _NOT_SOURCE_CODE.sub(" ", said)
    if _CODE_EXPLICIT.search(probe):
        return True
    return editor_in_front and bool(_CODING_VERB.match(said))


def instant_intercept(command: str) -> Optional[str]:
    """Which instant request this whole sentence is, if any. ``command`` is already cleaned."""
    said = (command or "").lower().strip().rstrip(".!?")
    said = re.sub(r"\s+please$|^please\s+", "", said)
    for name, pattern in _INSTANT:
        if pattern.fullmatch(said):
            return name
    return None


def strip_stray_hai(text: str) -> str:
    """"Message Papa on WhatsApp hai." → "Message Papa on WhatsApp." A lone "hai" tacked onto an
    otherwise English sentence is the recogniser's, not the person's; in Hinglish ("theek hai",
    "kya hota hai") it belongs and stays."""
    import re as _re
    from .speech_text import language_of

    m = _re.match(r"(?is)^(?P<body>.*?\w)[,\s]+hai[.!?]?$", (text or "").strip())
    if m and language_of(m.group("body")) == "en":
        return m.group("body") + "."
    return text


def speakable(reply: str) -> str:
    """A reply as it may be said aloud. Stack traces, provider errors and bracketed internal
    messages are shown on screen and summarised in one sentence — never read out."""
    text = (reply or "").strip()
    low = text.lower()
    if text.startswith("[voice can't reach the brain"):
        return "I can't reach my brain right now, sir. The backend isn't answering."
    if text.startswith("[") and ("error" in low[:40] or "exception" in low[:80]) or "traceback (most recent" in low:
        return "Something went wrong on my side, sir. The details are on screen."
    from .speech_text import is_filler, leaks_internals, without_internals
    if leaks_internals(text):
        text = without_internals(text) or "Sorry, sir, I got muddled on that one. Could you say it again?"
    parts = re.split(r"(?<=[.!?।])\s+", text)
    kept = [p for p in parts if p and not is_filler(p)]
    if kept and len(kept) < len(parts):
        text = " ".join(kept)
    return text


def _is_messaging_app(app: str) -> bool:
    a = (app or "").lower()
    return any(m in a for m in _MESSAGING_APPS)


class VoiceSession:
    def __init__(self, config: Config, on_event: Optional[EventCallback] = None) -> None:
        self.config = config
        self.on_event: EventCallback = on_event or (lambda kind, text="": None)
        # Why a capture came back empty, kept for whoever decides what to say about it, and a
        # guard so mending the microphone can never call itself.
        self._capture_problem = ""
        self._recovering = False
        self.backend = config.resolved_voice_backend()
        if self.backend == "local":
            from .local_wake import LocalWakeWord

            self.wake = LocalWakeWord(threshold=config.wake_threshold)
        else:
            self.wake = WakeWord(config.picovoice_access_key, config.wake_keyword)
        device = config.audio_input_device if config.audio_input_device >= 0 else -1
        # The echo-cancelled microphone when the jarvis-aec service is running (aec.py): Jarvis's
        # own voice and the video playing are subtracted before anything here listens.
        from . import aec
        node = aec.input_node() if device < 0 else ""
        self.mic = Microphone(self.wake.frame_length, device_index=device, pipewire_node=node)
        self.aec_active = node == aec.SOURCE
        if aec.mode() == "on" and not node:
            self.on_event("loading", "echo cancellation requested but jarvis-aec is not running "
                                     "— run scripts/install-aec-service.sh")
        self.sample_rate = self.wake.sample_rate
        self.frame_length = self.wake.frame_length
        self.threshold = float(config.vad_threshold)

        # Neural endpointing, with the old energy threshold as the fallback. Built here rather
        # than per-utterance so the ONNX session and its LSTM state are created once.
        self._endpointer = None
        self._partials = None
        if config.neural_endpointing:
            try:
                self._endpointer = endpoint.StreamingVad(self.sample_rate)
            except endpoint.SileroUnavailable:
                self._endpointer = None
        self._speaking = threading.Event()   # True while Jarvis's own audio is playing
        self._ptt = None                     # push-to-talk watcher, started in run()
        self._dictating = False              # while true, speech is typed rather than obeyed
        self._ptt_pressed = threading.Event()
        # System-wide dictation (jarvis/flow): one owner for the microphone, a key that starts
        # and stops it, and the engine that turns the audio into text in the focused field.
        from ..flow.mic import COORDINATOR
        self._coord = COORDINATOR
        self._flow = None
        self._gesture = None
        self._dict_keys = None
        self._dict_stop = threading.Event()
        self._dict_cancel = threading.Event()
        self._dict_key_at = 0.0
        self._preempted = False
        self._hud_queue: "queue.Queue" = queue.Queue()
        from collections import deque
        # ~300 ms of wake-listening audio, sized by time: the wake frames here are 80 ms, and a
        # count of 8 kept 640 ms — enough to pull in whatever was playing before the key.
        self._preroll: "deque" = deque(maxlen=max(1, round(0.3 * self.sample_rate / self.frame_length)))
        self._spoke_at = 0.0
        # Whoever reads a frame holds this for that frame; dictation holds it for its whole
        # capture. So dictation starts at once — while Jarvis is thinking nobody is reading at all
        # — and the wake listener, the assistant and barge-in simply wait until it is done.
        self._mic_lock = threading.Lock()
        self._dict_busy = threading.Lock()

        # The one conversation (conversation.py), published for the other processes and
        # measured: every transition is a metric, never with what was said.
        from . import conversation as _conv, voice_log
        self._conversation = _conv.SESSION
        self._conversation.window_s = float(config.follow_up_s)

        def _on_state(session) -> None:
            _conv.publish_to_file(session)
            voice_log.metric("state", state=session.state.value, turn=session.turns)
        self._conversation.on_change = _on_state

        # Barge-in: what the monitor heard from the onset of the person's voice, handed to the
        # next recording so the interruption's first words are not lost.
        self._barge: Optional[dict] = None
        self._handoff = threading.Event()
        self._barge_vad = None
        if config.neural_endpointing:
            try:
                self._barge_vad = endpoint.StreamingVad(self.sample_rate)
            except endpoint.SileroUnavailable:
                self._barge_vad = None
        self._played: list[str] = []          # sentences of the current utterance actually heard

        # Media: whether something is playing, refreshed in the background so the wake word and
        # barge-in can be stricter without a subprocess per frame.
        from .media import MEDIA
        self._media = MEDIA
        self._media_on = False

    def _read_frame(self):
        with self._mic_lock:
            return self.mic.read()

    # --- stages ----------------------------------------------------------
    async def _wait_for_wake_or_event(self):
        """Block until the wake word fires, push-to-talk is pressed, or a phone event is queued.

        Returns ('wake'|'event', payload).
        """
        self._coord.listen_for_wake()
        if self._conversation.state not in (ConvState.DICTATION, ConvState.SLEEPING,
                                            ConvState.FOLLOW_UP):
            self._conversation.listen_for_wake()
        from . import voice_log
        while True:
            if self._conversation.state is ConvState.FOLLOW_UP:
                # A dictation interrupted a conversation and has finished: carry on with it,
                # without the wake word.
                return ("resume", None)
            if self._ptt_pressed.is_set():
                # The caller emits "wake" for every path, so do not emit it again here — doing so
                # logged two wakes for one key press and looked like a double trigger.
                self._ptt_pressed.clear()
                return ("wake", None)
            if not self._events.empty():
                return ("event", self._events.get_nowait())
            frame = await asyncio.to_thread(self._read_frame)  # frees the loop for D-Bus signals
            # The last quarter second is kept: when the dictation key goes down, the first word
            # has often already started, and this is where it is.
            self._preroll.append(frame)
            # A video playing aloud with nothing cancelling it: the wake word needs a clearer,
            # longer match (local_wake.strict). With echo cancellation the video is subtracted.
            if hasattr(self.wake, "strict"):
                self.wake.strict = self._media_on and not self.aec_active
            if self.wake.process(frame):
                # Jarvis's own voice, or its tail in the room, is not the person saying his name.
                if self._speaking.is_set() or time.monotonic() - self._spoke_at < WAKE_COOLDOWN_S:
                    voice_log.metric("wake", reason="cooldown", aec=self.aec_active,
                                     media=self._media_on)
                    continue
                self._preroll.clear()               # the wake phrase never becomes dictation
                voice_log.metric("wake", reason="detected", aec=self.aec_active, media=self._media_on,
                                 confidence=float(getattr(self.wake, "last_score", 0.0)))
                return ("wake", None)

    def _watch_media(self) -> None:
        """Keep `_media_on` current (every 1.5 s) without a subprocess on every frame."""
        while True:
            try:
                self._media_on = self._media_playing()
            except Exception:  # noqa: BLE001
                self._media_on = False
            time.sleep(1.5)

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
                    if "@newsletter" in frm or "@g.us" in frm or m.get("fromMe"):
                        continue
                    sender, text = m.get("name", "someone"), m.get("text", "")
                    handled = True  # Backend PA daemon exclusively owns WhatsApp auto-replies.
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
        from ..preferences import notifications_enabled
        # Away state is set from another process (web /chat), so always read it from the shared file.
        away_now = away.is_away(self.config)
        if not notifications_enabled() and not away_now:
            return

        if event.get("type") == "announce":
            # A grouped announcement from the notification aggregator, spoken here so speech
            # stays on the main loop. Rechecked, because muting can happen while it waited.
            if not away_now and notifications_enabled():
                self._speak(event.get("text", ""))
            return

        if event.get("type") == "call":
            from ..notifications import call_announcement, speakable_sender
            who = speakable_sender(event.get("name") or event.get("number") or "")
            number = event.get("number")
            if "missed" in (event.get("event") or "").lower():
                self._speak(call_announcement(event.get("name", ""), number or "", "missed"))
            elif away_now and number:
                # Auto-attendant: text the caller that the user is unavailable.
                try:
                    if self._kc is not None:
                        self._kc.send_sms(number, away.oneliner(self.config))
                except Exception:  # noqa: BLE001
                    pass
                self._speak(f"{who} is calling. You're away, so I texted them you're unavailable.")
            else:
                self._speak(call_announcement(event.get("name", ""), number or "", "ringing"))
            return

        from ..notifications import NotificationEvent
        app = event.get("app", "phone")
        notice = NotificationEvent(app=app, sender=str(event.get("title", "") or ""),
                                   text=event.get("text", ""), thread=str(event.get("id", "") or ""))
        who = notice.who            # a name, never a raw JID or number
        msg = event.get("text", "")
        self.on_event("phone", f"{app} from {who}: {msg}")
        # Auto-attendant for repliable notifications (Instagram/SMS), unless already handled (WhatsApp).
        if away_now and event.get("repliable") and event.get("id") and not event.get("handled"):
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
            "id": event.get("id"), "repliable": event.get("repliable"), "at": time.time(),
        }
        if not away_now and notifications_enabled():  # while away, handle silently; muted = no readout
            # Not spoken yet: held until the conversation goes quiet, so a burst is said once.
            # _flush_notices speaks it. While dictating, ordinary messages wait for the summary.
            self._notices.add(notice, busy=self._dictating)

    async def _flush_notices(self) -> None:
        """Hand grouped announcements to the main loop as they come due."""
        while True:
            await asyncio.sleep(1.0)
            try:
                for announcement in self._notices.due():
                    await self._events.put({"type": "announce", "text": announcement.text})
            except Exception:  # noqa: BLE001 — a bad notification must not stop the watcher
                pass

    def _recover_and_listen(self, wait_s: float) -> Optional[str]:
        """Mend the microphone and take one more listen. Never recurses — one attempt per turn."""
        self._recovering = True
        try:
            healthy, what = inputs.recover(
                self.config.audio_input_device,
                note=lambda message: self.on_event("timing", message))
            self.on_event("timing", what)
            if not healthy:
                self._capture_problem = (
                    "Every microphone on this machine is pinned at full volume, sir, and "
                    "restarting the audio did not clear it. Something outside Jarvis has the "
                    "sound system wedged.")
                return None
            # The stream in hand still points at the old device; the recovery replaced what is
            # underneath it.
            try:
                self.mic.reopen()
            except Exception as exc:  # noqa: BLE001
                self._capture_problem = f"I couldn't reopen the microphone — {exc}"
                return None
            self._capture_problem = ""
            return self._record_transcript(wait_s)
        finally:
            self._recovering = False

    def _record_transcript(self, wait_s: float, prefix: Optional[list] = None) -> Optional[str]:
        """An assistant turn's capture. Pressing the dictation key hands the microphone over
        mid-capture: nothing captured so far becomes a command.

        ``prefix``: frames already heard — the start of an interruption, captured by the barge-in
        monitor while Jarvis was still speaking — which the capture begins with."""
        from ..flow.mic import Preempted
        try:
            self._coord.assistant_capture()
            return self._record_transcript_inner(wait_s, prefix or [])
        except Preempted:
            self._preempted = True
            return None

    # --- system-wide dictation --------------------------------------------------------------
    def _dictation_event(self, state: str, **details) -> None:
        """Overlay events, sent from a worker so the capture loop never waits on HTTP."""
        import json as _json
        self._hud_queue.put(("dictation", _json.dumps({"state": state, **details}, ensure_ascii=False)))

    def _hud_worker(self) -> None:
        while True:
            kind, text = self._hud_queue.get()
            try:
                self.on_event(kind, text)
            except Exception:  # noqa: BLE001
                pass

    def _start_dictation_keys(self) -> None:
        from ..flow import keys as fkeys
        from ..flow.engine import DictationEngine
        threading.Thread(target=self._hud_worker, daemon=True).start()
        self._flow = DictationEngine(emit=self._dictation_event)
        # Start the focus bridge now: it follows focus changes as they happen, and one that starts
        # at the key press has missed them (a browser can go on claiming "active" after another
        # window took over).
        from ..flow.focus import BRIDGE
        threading.Thread(target=BRIDGE.request, args=("ping",), daemon=True).start()
        key, mode, cancel_key = fkeys.configured()
        if hotkey.resolve_key(key) is None:
            self.on_event("loading", "dictation key disabled (JARVIS_DICTATION_KEY=none)")
            return
        problems = fkeys.conflicts(key, self.config.ptt_key)
        if problems:
            self.on_event("loading", "dictation key problem — " + " ".join(problems))
            if any("push-to-talk" in p or "cancel key" in p for p in problems):
                return

        def on_start():
            self._dict_key_at = time.monotonic()
            self._dict_stop.clear()
            self._dict_cancel.clear()
            if self._speaking.is_set():
                self.stop_speaking()           # Jarvis's own voice must not be transcribed
            self._conversation.dictation_started()   # resumed when the dictation is done
            self._coord.request_dictation()    # an assistant capture in progress ends now
            threading.Thread(target=self._dictation_worker, daemon=True, name="dictation").start()

        def on_cancel():
            if self._flow is not None and self._flow.pending:
                self._flow.discard_pending()
            self._dict_cancel.set()
            self._coord.cancel_request()

        def on_confirm():
            threading.Thread(target=self._flow.confirm_pending, daemon=True).start()

        self._gesture = fkeys.Gesture(mode, on_start=on_start, on_stop=self._dict_stop.set,
                                      on_cancel=on_cancel, on_confirm=on_confirm)
        self._dict_keys = fkeys.start(key, self._gesture, cancel_key)
        if self._dict_keys is not None:
            self.on_event("loading", f"dictation: {key} ({mode}) · cancel: {cancel_key}")
        else:
            self.on_event("loading", f"dictation key unavailable — {hotkey.diagnose(key)}")

    def _dictation_worker(self) -> None:
        """Started by the key, on its own thread: dictation does not wait for the voice loop.

        Found live: a YouTube lecture playing aloud woke the assistant, which spent 4.7 s
        thinking and then spoke — and a dictation pressed meanwhile waited for all of it, so the
        words were gone before the microphone was read. Now the key stops Jarvis speaking, takes
        the microphone (at most one frame's wait) and records straight away; an assistant capture
        in progress ends at once, and the wake listener waits until the capture is finished.
        """
        if self._flow is None:
            self._conversation.dictation_ended()
            return
        if not self._dict_busy.acquire(blocking=False):
            return                  # one dictation at a time; the running one resumes the state
        try:
            # Let Jarvis's own voice finish stopping, so its tail is not the first thing heard.
            for _ in range(20):
                if not self._speaking.is_set():
                    break
                time.sleep(0.025)
            # Audio heard just before the key: included unless it could be Jarvis's own voice.
            preroll = [] if (self._speaking.is_set() or time.monotonic() - self._spoke_at < 1.0) \
                else list(self._preroll)
            self._preroll.clear()
            if not self._mic_lock.acquire(timeout=2.0):
                self._dictation_event("error", message="The microphone is busy — try again")
                if self._gesture is not None:
                    self._gesture.reset()
                return
            try:
                self._coord.begin_dictation()
                pcm = self._flow.capture(self.mic.read, self._dict_stop, self._dict_cancel,
                                         rate=self.sample_rate, key_at=self._dict_key_at, preroll=preroll)
            finally:
                self._mic_lock.release()                 # wake listening resumes while text is made
            self._coord.processing()
            result = self._flow.finish(pcm, rate=self.sample_rate)
            del pcm
            if result.get("status") in {"preview", "confirm"} and self._gesture is not None:
                self._gesture.await_confirmation()
                gesture, flow = self._gesture, self._flow

                def expire():
                    if gesture.state == "confirming":
                        gesture.reset()
                        flow.discard_pending()
                threading.Timer(16.0, expire).start()
            elif self._gesture is not None and self._gesture.state in {"held", "hands_free"}:
                self._gesture.reset()
            m = self._flow.metrics
            # Timings only: what was said never reaches the journal.
            self.on_event("timing", "dictation " + " ".join(f"{k}={v}" for k, v in m.items())
                          + f" status={result.get('status')}")
        except Exception as exc:  # noqa: BLE001 — a broken dictation must still give the mic back
            self._dictation_event("error", message=f"Dictation failed ({type(exc).__name__})")
            if self._gesture is not None:
                self._gesture.reset()
        finally:
            self._coord.end_dictation()
            self._conversation.dictation_ended()
            self._dict_busy.release()

    def _record_transcript_inner(self, wait_s: float, prefix: Optional[list] = None) -> Optional[str]:
        capture_start = time.monotonic()
        speech_end = [0.0]
        pending = list(prefix or [])
        live = self._coord.guard(self._read_frame)

        def read():
            return pending.pop(0) if pending else live()

        def speech_started() -> None:
            if self._conversation.state is not ConvState.DICTATION:
                self._conversation.endpointing()
            self.on_event("listening")

        if self._endpointer is not None:
            # Neural endpointing: fires ~90 ms after you actually stop, instead of waiting out a
            # fixed 2 s of silence. Partials stream to the HUD while you are still talking.
            partial = self._partials
            if partial is not None:
                partial.reset()

            def on_partial(pcm_so_far: bytes) -> None:
                if partial is not None:
                    partial.submit(pcm_so_far)

            # What the device delivered, whatever the endpointer made of it. A microphone that
            # hands over nothing but empty frames is a different problem from someone mumbling,
            # and the two used to produce the same apology.
            heard = inputs.Heard()

            def watch_level(level: float, probability: float) -> None:
                heard.note(level)
                levels.publish(level, "listening", probability)

            pcm = endpoint.record_utterance(
                read,
                sample_rate=self.sample_rate,
                frame_length=self.frame_length,
                silence_ms=self.config.endpoint_hangover_ms,
                max_s=self.config.max_utterance_s,
                wait_s=wait_s,
                vad=self._endpointer,
                on_speech_start=speech_started,
                on_partial=on_partial if partial is not None else None,
                on_level=watch_level,
                partial_every_ms=self.config.partial_every_ms,
            )
            speech_end[0] = time.monotonic()
            if partial is not None:
                partial.cancel()
        else:
            pcm = vad.record_utterance(
                read,
                sample_rate=self.sample_rate,
                frame_length=self.frame_length,
                threshold=self.threshold,
                silence_ms=self.config.silence_ms,
                max_s=self.config.max_utterance_s,
                wait_s=wait_s,
            )
            speech_end[0] = time.monotonic()

        if not pcm:
            # nothing detected → mic muted/wrong device, or the threshold is too high
            # Silent and too-quiet look identical from here — nothing was captured — and they
            # are fixed completely differently, so they are told apart and named.
            wrong = inputs.verdict(heard) if self._endpointer else ""
            self.on_event("timing", f"no audio captured (endpointing="
                                    f"{'silero' if self._endpointer else f'rms@{self.threshold}'}"
                                    f"{', microphone ' + wrong if wrong else ''}"
                                    f"{f', loudest frame {heard.peak:.4f}' if heard.frames else ''})")
            # Remembered rather than spoken here: this returns None for several reasons and the
            # caller is the one that decides what to say about it.
            self._capture_problem = inputs.explain(heard, self.config.audio_input_device)
            if wrong == "saturated" and not self._recovering:
                # A device that is pinned at full scale will stay that way for every turn until
                # something is done about it, and the person talking has no way to know. Put it
                # right and listen again, once, rather than making them ask.
                return self._recover_and_listen(wait_s)
            return None
        self._capture_problem = ""
        duration = len(pcm) / 2 / self.sample_rate
        start = time.monotonic()
        text = self._transcribe(pcm)
        stt_s = time.monotonic() - start
        self._speech_ended_at = speech_end[0]
        from . import voice_log
        words = len(re.findall(r"\w+", text or ""))
        voice_log.metric("stt", ms=stt_s * 1000, s=duration, words=words, aec=self.aec_active)
        # Timings and a word count; the words themselves are the "transcript" event's, which the
        # journal redacts unless diagnostics are on.
        self.on_event("timing",
                      f"heard {duration:.1f}s clip, endpoint+capture {speech_end[0]-capture_start:.1f}s, "
                      f"transcribed in {stt_s:.2f}s "
                      f"-> {f'{words} words' if text else 'EMPTY (STT found no words)'}")
        if text:
            self.on_event("transcript", "(dictated text)" if self._dictating else text)   # committed transcript replaces any partial
        return text

    def _transcribe(self, pcm: bytes) -> str:
        """Groq's Whisper large-v3-turbo first, the local model if it fails or is not set up.

        Measured on the end-to-end run: the local `small` model turned "Ab ye step Hinglish mein
        samjhao" into "अब ये स्थ तब हिंगलिष में समजाओ" and took 13.7 s on a busy machine, so the
        request was not understood. Groq heard it as "अब ये step English में समझाओ" in 0.26 s,
        and every English command in that run in 0.25–0.43 s. The dictation key already used this
        order; the assistant now does too. ``JARVIS_ASSISTANT_STT`` sets it ("local" alone keeps
        everything on this machine).
        """
        if self.backend != "local":
            return stt.transcribe(pcm, self.config.deepgram_api_key, self.sample_rate,
                                  self.config.stt_model)
        from ..flow import stt as flow_stt
        from . import local_stt, voice_log

        def local(pcm_, rate, vocabulary, language):
            return local_stt.transcribe(pcm_, rate, self.config.whisper_model,
                                        self.config.whisper_beam, self.config.stt_language,
                                        self.config.stt_vocabulary), ""

        def groq(pcm_, rate, vocabulary, language):
            # The command vocabulary only. Primed with a line of Roman Hinglish ("Haan theek
            # hai…") Whisper began ending English commands with "hai" — found live: "Message Papa
            # on WhatsApp hai" made a recipient of "Papa on WhatsApp hai". Hindi then arrives in
            # Devanagari, which every handler accepts.
            prompt = self.config.stt_vocabulary or local_stt.DEFAULT_VOCABULARY
            text, lang = flow_stt.groq(pcm_, rate, vocabulary=prompt[:800], timeout=6.0)
            return strip_stray_hai(text), lang

        order = [n.strip() for n in os.environ.get("JARVIS_ASSISTANT_STT", "groq,local").split(",")
                 if n.strip() in {"groq", "local"}] or ["local"]
        heard = flow_stt.transcribe(pcm, self.sample_rate, providers=order,
                                    table={"groq": groq, "local": local})
        voice_log.metric("stt_provider", provider=heard.provider or "none",
                         fallback=bool(heard.failures), ms=heard.seconds * 1000,
                         lang=heard.language or "")
        return heard.text

    @staticmethod
    def _media_playing() -> bool:
        """Is a video or song playing aloud? (MPRIS: browsers, Spotify, players.)"""
        import subprocess
        try:
            out = subprocess.run(["playerctl", "-a", "status"], capture_output=True, text=True, timeout=1).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return "Playing" in out

    def _barge_in_monitor(self, stop: threading.Event, first_audio: Optional[threading.Event] = None) -> None:
        """Listen while Jarvis talks (and while he is still thinking): stop him the moment the
        person starts talking over him, and keep what they are saying for the next capture.

        The decision is bargein.py's — Silero's speech probability above a background that is
        tracked continuously (Jarvis's echo, the video), for 2–4 frames depending on whether the
        echo is being cancelled and whether media is playing. The loudness-only version it
        replaces needed a full second, and stood down entirely while any video played.
        """
        from . import bargein, voice_log
        media = self._media_on
        speaking_policy = bargein.policy(self.aec_active, media)
        # Before the first audio a trigger cancels a request that is still being answered, which
        # costs more than cutting a sentence short: two more frames of speech are wanted then.
        from dataclasses import replace
        detector = bargein.BargeInDetector(
            replace(speaking_policy, frames_needed=speaking_policy.frames_needed + 2),
            frame_s=self.frame_length / self.sample_rate)
        detector.arm()                      # the room before Jarvis says anything
        vad_ = self._barge_vad
        if vad_ is not None:
            vad_.reset()
        from collections import deque
        ring: "deque" = deque(maxlen=max(4, round(0.6 * self.sample_rate / self.frame_length)))
        audio_armed = False
        while not stop.is_set():
            if first_audio is not None and first_audio.is_set() and not audio_armed:
                detector.policy = speaking_policy
                detector.arm()              # his echo arrives now: learn it before judging
                audio_armed = True
            try:
                frame = self._read_frame()
            except Exception:  # noqa: BLE001
                return
            if stop.is_set():
                return
            ring.append(frame)
            if vad_ is not None:
                probs = vad_.push(frame)
                probability = sum(probs) / len(probs) if probs else 0.0
            else:
                probability = 1.0           # no Silero: loudness alone, with the stricter margin
            now = time.monotonic()
            if not detector.feed(bargein.rms(frame), probability, now):
                continue
            # The person is talking. Stop Jarvis first; everything else can wait a frame.
            stop.set()
            onset_frames = detector.policy.frames_needed + 2
            self._barge = {"onset_at": detector.onset_at, "triggered_at": now,
                           "frames": list(ring)[-onset_frames:], "policy":
                           f"{'aec' if self.aec_active else 'raw'}{'+media' if media else ''}",
                           "detector_ms": (detector.decided_in_s or 0) * 1000,
                           "during": "speaking" if audio_armed else "thinking"}
            self.on_event("barge_in")
            # Keep listening until the loop takes the microphone over, so nothing said between
            # the decision and the next capture is lost (at most 3 s, if nobody takes it).
            deadline = now + 3.0
            while not self._handoff.is_set() and time.monotonic() < deadline:
                try:
                    self._barge["frames"].append(self._read_frame())
                except Exception:  # noqa: BLE001
                    break
            voice_log.metric("barge_in_detected", policy=self._barge["policy"],
                             detector_ms=self._barge["detector_ms"], stage=self._barge["during"])
            return

    def _take_barge(self) -> Optional[dict]:
        """The interruption the monitor caught, handed over once. None when there was none."""
        barge, self._barge = self._barge, None
        return barge

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """The written reply as it should be said (speech_text.normalize): no markdown, no
        secrets, no URL or phone number read out character by character."""
        from .speech_text import normalize
        return normalize(text)

    def _note_said(self, text: str) -> None:
        """What Jarvis just said, kept briefly so its echo is not taken for the person."""
        said = getattr(self, "_recently_said", None)
        if said is None:
            said = self._recently_said = []
        now = time.monotonic()
        for piece in re.split(r"(?<=[.!?।])\s+", text or ""):
            words = _words(piece)
            if words:
                said.append((now, words))
        del said[:-40]

    def _is_own_echo(self, transcript: str) -> bool:
        heard = _words(transcript)
        if not heard:
            return False
        now = time.monotonic()
        recent = [w for at, w in getattr(self, "_recently_said", []) if now - at < ECHO_WINDOW_S]
        joined = " ".join(heard)
        for words in recent:
            said = " ".join(words)
            if joined == said:
                return True
            # Three words or more inside something just said: "what would you like" inside "what
            # would you like to do next". Shorter ones ("clear", "yes") are the person's.
            if len(heard) >= 3 and joined in said:
                return True
            if len(heard) >= 3:
                import difflib
                if difflib.SequenceMatcher(None, joined, said).ratio() >= 0.82:
                    return True
        return False

    def _speak(self, text: str, force: bool = False) -> None:
        text = self._clean_for_speech(text)
        if not text.strip():
            return
        self._note_said(text)
        self._speaking_text = text

        def play(stop, on_first_audio, on_level):
            if self.backend == "local":
                from . import local_tts

                local_tts.speak(
                    text, self.config.piper_model, self.config.audio_output_device,
                    stop_event=stop, on_first_audio=on_first_audio, on_level=on_level,
                    on_played=self._played.append,
                )
            else:
                tts.speak(
                    text, self.config.elevenlabs_api_key, self.config.tts_voice_id,
                    self.config.tts_model_id, self.sample_rate,
                    self.config.audio_output_device, stop_event=stop,
                )

        self._while_speaking(play, announce=text, force=force)

    def _speak_phrases(self, phrases: list[str], on_start) -> "object":
        """Speak a lesson's phrases, each one reported as it becomes audible.

        The teaching overlay draws each phrase's picture from ``on_start(i, at_ms)``; the barge-in
        monitor, the stop token and the speaking flag are the same as for any reply, because it
        runs inside ``_while_speaking``. Returns how far it got (teach.runner.Spoken).
        """
        from ..teach.runner import Spoken

        started: list[int] = []

        def begin(i: int, at_ms: float) -> None:
            started.append(i)
            self._note_said(phrases[i])
            # The words reach the HUD as they are said, like a streamed reply's.
            self.on_event("reply", phrases[i])
            on_start(i, at_ms)

        def play(stop, on_first_audio, on_level):
            if self.backend == "local":
                from . import local_tts

                local_tts.speak_segments(
                    phrases, self.config.piper_model, self.config.audio_output_device,
                    stop_event=stop, on_first_audio=on_first_audio, on_level=on_level,
                    on_start=begin, on_played=self._played.append,
                )
                return
            # The hosted voice takes one utterance at a time: each phrase is its own, and its
            # picture goes out as the request is made — later than the local path, by the
            # service's first-audio time.
            for i, text in enumerate(phrases):
                if stop.is_set():
                    break
                begin(i, time.time() * 1000 + 250)
                tts.speak(text, self.config.elevenlabs_api_key, self.config.tts_voice_id,
                          self.config.tts_model_id, self.sample_rate, self.config.audio_output_device,
                          stop_event=stop)
                if not stop.is_set():
                    self._played.append(text)

        self._while_speaking(play, announce=phrases[0] if phrases else "")
        finished = len(self._played)
        return Spoken(started=len(started), finished=finished,
                      interrupted=finished < len(phrases))

    async def _teach_turn(self, transcript: str):
        """A request for the teaching overlay, handled here so its speech and pictures stay in
        step. None when it is not one; otherwise the Reply (already spoken, or to be said)."""
        try:
            from ..teach import assistant
            if not assistant.wants(transcript):
                return None
        except Exception:  # noqa: BLE001 — the overlay is optional; the voice is not
            return None
        self._conversation.acting("teach")
        return await assistant.handle(transcript, speaker=_LessonVoice(self))

    def _unsaid(self, full: str) -> str:
        """What of ``full`` was not heard before an interruption: the sentences after the last
        one that played to its end."""
        from .local_tts import sentences_as_they_arrive
        sentences = list(sentences_as_they_arrive([(full or "") + " "]))
        return " ".join(sentences[len(self._played):]).strip()

    async def _ask_and_say(self, agent, prompt: str) -> tuple[str, bool]:
        """Ask the brain and speak the answer while it is still being written.

        Returns (reply, handled). `handled` says the answer has already been spoken and shown,
        sentence by sentence — the caller must then do neither, or the whole reply is repeated
        aloud and printed twice.

        The two run at once: the brain fills a queue with fragments and the speaking thread
        drains it, so the first sentence is heard while the last is still being generated. What
        is returned is the finished reply, because everything downstream — the HUD, the journal,
        the follow-up — still wants the whole thing.

        Two things happen while waiting. If nothing has come back after ACK_AFTER_S, a short
        cached "Give me a moment" is said, once — never for the instant commands, which answer
        well inside that. And the microphone stays open (the barge-in monitor runs from the
        start, not from the first word): if the person talks, the request is abandoned here and
        what they said is handled next.

        Falls back to waiting for the reply whenever streaming is not available, which is not a
        rare path: the hosted voice takes whole utterances, and a provider can refuse to stream.
        """
        if self.backend != "local":
            return await agent.send(prompt), False

        fragments: "queue.Queue[Optional[str]]" = queue.Queue()
        acked: list[str] = []

        def drain():
            first = True
            while True:
                if first:
                    try:
                        piece = fragments.get(timeout=ACK_AFTER_S)
                    except queue.Empty:
                        from .local_tts import ACKNOWLEDGEMENTS
                        from .speech_text import with_honorific
                        ack = with_honorific(ACKNOWLEDGEMENTS[1])
                        acked.append(ack)
                        if hasattr(self, "_note_said"):
                            self._note_said(ack)
                        yield ack + " "
                        piece = fragments.get()
                    first = False
                else:
                    piece = fragments.get()
                if piece is None:
                    return
                yield piece

        spoken: list[str] = []

        def show(sentence: str) -> None:
            # Each sentence reaches the HUD as it is spoken, rather than the whole reply
            # arriving after the talking has stopped — which is what happened when the text was
            # only published once the brain returned: you heard the answer with nothing on
            # screen, and then read it as Jarvis fell silent.
            if acked and sentence.strip() in acked[0]:
                return
            from .speech_text import is_filler
            if is_filler(sentence):
                return                  # not said (local_tts drops it too), so not shown
            self.on_event("reply", sentence)
            note = getattr(self, "_note_said", None)     # bookkeeping never stands in the way
            if note:
                note(sentence)

        speaker = threading.Thread(
            target=lambda: spoken.append(
                self._speak_as_written(drain(), on_sentence=show)), daemon=True)

        agent.on_reply_delta = fragments.put
        reply = ""
        try:
            speaker.start()
            task = asyncio.ensure_future(agent.send(prompt))
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.05)
                if done:
                    reply = task.result()
                    break
                if getattr(self, "_barge", None) is not None:
                    # Talked over while still thinking or mid-answer: this request is dropped
                    # here. The brain may finish what it started; its words are not spoken.
                    task.cancel()
                    break
        finally:
            agent.on_reply_delta = None
            fragments.put(None)          # closes the stream whether it finished or failed
            speaker.join(timeout=30)

        if getattr(self, "_barge", None) is not None and not reply:
            return "", True              # the interruption is the next thing to handle

        # What was actually said, versus what the brain ended up returning. They differ when a
        # tool ran: the loop speaks nothing for those rounds and the reply is assembled
        # afterwards, so there is still something left to say.
        already = "".join(spoken).strip()
        if acked and already.startswith(acked[0]):
            already = already[len(acked[0]):].strip()
        remaining = (reply or "").strip()
        if already and remaining and remaining.startswith(already[:40]):
            return reply, True    # said and shown already; the caller must do neither
        if remaining and not already:
            return reply, False   # nothing streamed — a tool ran, so the caller says it
        if remaining and already and not remaining.startswith(already[:40]):
            # The brain's final answer is not what was streamed — a tool ran after the text, or
            # the answer was rewritten. Say the real one; a half-answer left hanging is worse
            # than a repeated word.
            return reply, False
        return reply, True

    def _speak_as_written(self, pieces, on_sentence=None, force: bool = False) -> str:
        """Speak a reply while the model is still writing it. Returns what was said.

        Only the local voice can do this: the hosted one is asked for a whole utterance at a
        time. When it is in use the caller falls back to waiting for the reply, which is how it
        behaved before any of this existed.
        """
        said: list[str] = []

        def play(stop, on_first_audio, on_level):
            from . import local_tts

            said.append(local_tts.speak_as_it_arrives(
                pieces, self.config.piper_model, self.config.audio_output_device,
                stop_event=stop, on_first_audio=on_first_audio, on_level=on_level,
                on_sentence=on_sentence, on_played=self._played.append,
            ))

        # The text is not known in advance, so the HUD is told when the first audio arrives
        # rather than before it — which is also the first moment there is anything to show.
        self._while_speaking(play, announce=None, force=force)
        return "".join(said)

    def _while_speaking(self, play, announce, force: bool = False) -> None:
        """Run `play` with everything that has to be true while Jarvis is talking.

        Barge-in, the stop token, the speaking flag and the HUD level all have to be set up
        before a sound is made and taken down afterwards whatever happens. Two copies of that
        would eventually differ in one of them, and the one that differs would be the one that
        leaves the HUD animating a level nothing is producing.
        """
        if not force:
            from .. import power
            if power.asleep():          # muted while asleep; wake/sleep lines pass force=True
                return
        stop = self._stop_speaking = threading.Event()
        first_audio = threading.Event()
        self._played = []
        self._barge = None
        self._handoff.clear()
        monitor: Optional[threading.Thread] = None
        if self.config.enable_barge_in:
            monitor = threading.Thread(target=self._barge_in_monitor, args=(stop, first_audio),
                                       daemon=True)
            monitor.start()
        # A stop requested before this utterance started must not silence it.
        stop_token = speech_control.token()
        watcher = threading.Thread(
            target=self._watch_stop_request, args=(stop, stop_token), daemon=True
        )
        watcher.start()
        in_turn = self._conversation.state is not ConvState.DICTATION
        started = time.monotonic()

        def on_first_audio() -> None:
            # Speaking from the first sound, not from when the reply was asked for: while the
            # brain is still thinking nothing is playing, and the wake cooldown, the dictation
            # pre-roll and the web server's "is he speaking" all mean sound.
            self._speaking.set()
            speech_control.set_speaking(True)
            first_audio.set()
            if in_turn and self._conversation.active:
                self._conversation.speaking()
            from . import voice_log
            voice_log.metric("tts", ms=(time.monotonic() - started) * 1000)
            ended = getattr(self, "_speech_ended_at", 0.0)
            if ended and self._conversation.active:
                voice_log.metric("turn", end_to_first_audio_ms=(time.monotonic() - ended) * 1000)
                self._speech_ended_at = 0.0
            self.on_event("speaking", announce) if announce is not None else self.on_event("speaking")

        try:
            play(stop, on_first_audio, lambda lvl: levels.publish(lvl, "speaking"))
        finally:
            stop.set()
            self._speaking.clear()
            self._spoke_at = time.monotonic()      # its echo must not be dictation pre-roll
            speech_control.set_speaking(False)
            levels.publish(0.0, "")     # idle: stop the HUD animating a level nothing is producing
            barged = self._barge is not None
            if barged:
                from .local_tts import PLAYBACK
                stopped_at = PLAYBACK["stopped_at"]
                # A stop time from before this onset is a previous utterance's.
                self._barge["stopped_at"] = stopped_at if stopped_at >= self._barge["onset_at"] \
                    else time.monotonic()
                self._handoff.set()     # the monitor stops listening; the frames are ours
            if monitor is not None:
                monitor.join(timeout=1.0)
            if not barged:
                self._flush_mic()       # the tail of his own voice — but never the person's
            self.on_event("spoken")

    def _watch_stop_request(self, stop: threading.Event, since: int) -> None:
        """Poll the cross-process stop signal while speaking; cheap and ends with the utterance."""
        while not stop.wait(0.1):
            if speech_control.should_stop(since):
                self.on_event("barge_in")
                stop.set()
                return

    def stop_speaking(self) -> bool:
        """Cut the current utterance short. Distinct from cancelling the task that produced it."""
        ev = getattr(self, "_stop_speaking", None)
        if ev is not None and not ev.is_set():
            ev.set()
            return True
        return False

    def _flush_mic(self, frames: int = 8) -> None:
        """Drop buffered microphone audio, and reset the endpointer's state with it.

        Two callers, two reasons. After speaking: the tail of Jarvis's own voice is still in the
        input buffer and would be read back as the start of the next utterance. After the wake
        word: the tail of "Hey Jarvis" is still there, and the endpointer's preroll deliberately
        keeps a moment of audio from *before* speech starts, so the wake phrase ended up inside
        the command — "Hey Jarvis, open Netflix" transcribed as "HR was OpenNet Flix", which
        matches nothing.

        Resetting the VAD state matters too, or its speech probability stays high into the turn.
        """
        try:
            for _ in range(max(0, frames)):
                self._read_frame()
        except Exception:  # noqa: BLE001 - a flush failure must not break the turn
            pass
        if self._endpointer is not None:
            self._endpointer.reset()

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

    async def _watch_coding_jobs(self) -> None:
        """Speak the result of a Jarvis-started coding job; a soft chime for any job finishing.

        Manually-started (external) jobs get the chime + HUD state only — no spoken summary.
        """
        import httpx
        from ..agent.remote import web_base
        from ..jobs import sound

        seen: dict[str, str] = {}
        first_pass = True
        while True:
            await asyncio.sleep(6)
            try:
                jobs = httpx.get(f"{web_base()}/coding/jobs", timeout=4).json().get("jobs", [])
            except Exception:  # noqa: BLE001
                continue
            terminal = {"completed", "failed", "cancelled"}
            for job in jobs:
                jid, status = job.get("id", ""), job.get("status", "")
                was = seen.get(jid)
                seen[jid] = status
                if first_pass or was == status or status not in terminal:
                    continue
                sound.chime()
                self.on_event("coding", f"{job.get('provider', 'coding').title()} {status}: {job.get('summary', '')[:120]}")
                if not job.get("external"):
                    where = os.path.basename(job.get("workspace", "").rstrip("/")) or "your project"
                    verb = "finished" if status == "completed" else status
                    self._speak(f"VS Code prompt {verb}, sir. {job.get('provider', 'The agent').title()} "
                                f"returned on {where} with: {job.get('summary') or 'no notable output'}")
            first_pass = False

    async def _keep_study_mode(self) -> None:
        """Keep the distractions shut for as long as study mode is on.

        Closing Discord once achieves nothing — it is open again within a minute, because the
        hand that opens it is not really asking a question. So it is closed again, quietly, and
        only mentioned when something actually came back.

        Every eight seconds rather than twenty, because of Shorts: a Short is often over inside
        twenty seconds, so a sweep on that interval lets you watch the whole thing before it
        closes — which is the same as not closing it. A sweep is one tab listing over loopback;
        doing it more often costs nothing worth counting.
        """
        from ..modes import study

        while True:
            await asyncio.sleep(8.0)
            if not study.on():
                continue
            try:
                apps, tabs = await study.enforce()
            except Exception:  # noqa: BLE001 — a failed sweep must not end the watcher
                continue
            if apps or tabs:
                came_back = ", ".join(list(dict.fromkeys(apps + tabs))[:3])
                self.on_event("study", f"closed again: {came_back}")

    async def _watch_coding_agents(self) -> None:
        """Show process activity on the HUD without claiming a task has finished.

        This colours the dot and says nothing. CPU going quiet is not a completion signal —
        Claude waits on tools and thinks between bursts, and calling any of that "finished" is
        what produced the repeated false alerts. Turn endings are announced from Claude's own
        Stop hook in `_watch_claude_stops`, which fires once, exactly when a turn ends.

        Nothing here reads the terminal either. It used to, to tell a finished turn from a
        waiting one, and the way it did that was to open the command palette, run Terminal:
        Select All, copy and press escape — every few seconds, while you were typing into it.
        `presence` now reads the agents' own transcripts instead, so the dot is better informed
        and the clipboard is left alone.
        """
        from ..coding import presence

        while True:
            await asyncio.sleep(4.0)
            try:
                now = await asyncio.to_thread(presence.look)
                # Drained and discarded: the dot is this watcher's job, the announcement is the
                # Stop hook's. Left undrained they would pile up for ever.
                presence.drain_events()
            except Exception:  # noqa: BLE001 — a failed look must not end the watcher
                continue
            self.on_event("agent", f"{now.kind}:{now.agent}")

    async def _watch_claude_stops(self) -> None:
        """Announce actual Claude turn endings from its Stop hook, once per message."""
        from ..coding import claude_stop

        offset = claude_stop.end_offset()  # old events on restart are not new completions
        seen: set[str] = set()
        while True:
            await asyncio.sleep(3.0)
            events, offset = await asyncio.to_thread(claude_stop.read_since, offset)
            for event in events:
                key = event.get("id", "")
                if key and key not in seen:
                    seen.add(key)
                    self.on_event("coding", "Claude finished a turn")
                    self._speak("Claude has finished, sir.")

    async def _watch_coding_terminal(self) -> None:
        """Say what the agent in the editor concluded, once it has stopped writing.

        Only looks when an answer is actually expected — but "only when expected" still meant
        copying the terminal every six seconds for up to fifteen minutes, by opening the command
        palette each time. Where the agent keeps a transcript, its own final message is read
        instead and nothing touches the clipboard at all. The terminal stays as the fallback for
        agents that do not write one.
        """
        from ..coding import presence, roster, terminal, transcripts, watch

        def screen() -> str:
            """The agent's answer: from its transcript when that is unambiguous, else the screen.

            `watch.expect` records which agent was prompted but not where, so the transcript can
            only be trusted when that agent has exactly one session open. With two, there is no
            way to tell the one Jarvis typed into from the one you are using yourself, and
            reading the wrong session's answer aloud is worse than borrowing the clipboard.

            In the ordinary case — one agent in the editor — this is the whole fifteen minutes of
            six-second clipboard copies replaced by reading a file.
            """
            try:
                expecting = roster.canonical_name(watch.waiting_for() or "")
                candidates = [
                    cwd for cwd in presence.working_directories(expecting)
                    if transcripts.transcript_for(cwd)
                ]
                if len(candidates) == 1:
                    # Authoritative: either the turn has ended and this is the answer, or it has
                    # not and there is nothing to say yet. Falling back to the terminal here
                    # would reintroduce the copying this replaced.
                    return transcripts.final_answer(candidates[0]) or ""
            except Exception:  # noqa: BLE001
                pass
            return terminal.tail(80) or ""

        while True:
            await asyncio.sleep(6.0)
            if watch.waiting_for() is None:
                continue
            try:
                done = await asyncio.to_thread(watch.check, screen)
            except Exception:  # noqa: BLE001 — a failed look must not end the watcher
                continue
            if done is None:
                continue
            spoken_who = roster.spoken_name(done.agent)
            if done.said:
                self.on_event("coding", f"{done.agent}: {done.said[:160]}")
                self._speak(f"{spoken_who} says: {done.said}")
            opened = False
            if done.link:
                # The thing you wanted next. Opened rather than read out, because a URL spoken
                # aloud is unusable and this one is one click of work.
                try:
                    from ..integrations import apps
                    await asyncio.to_thread(apps.open_url, done.link)
                    self._speak("I've opened the link it printed.")
                    opened = True
                except Exception:  # noqa: BLE001
                    pass
            if not done.said and not opened:
                self.on_event("coding", f"{done.agent}: finished")
                self._speak(f"{spoken_who} has finished, sir.")

    async def _watch_power(self) -> None:
        """Announce the charger going in or coming out, with the battery level."""
        from ..integrations.power_supply import PowerWatcher
        from ..preferences import notifications_enabled

        watcher = PowerWatcher()
        while True:
            await asyncio.sleep(3)
            try:
                message = await asyncio.to_thread(watcher.poll)
            except Exception:  # noqa: BLE001
                continue
            if not message:
                continue
            self.on_event("power", message)
            if notifications_enabled():
                self._speak(message)

    async def _watch_meet(self) -> None:
        """Announce once when a Meet the bot joined finishes, with its summary."""
        import httpx
        from ..agent.remote import web_base

        announced = ""
        while True:
            await asyncio.sleep(10)
            try:
                s = httpx.get(f"{web_base()}/meet/status", timeout=4).json()
            except Exception:  # noqa: BLE001
                continue
            key = f"{s.get('started_at')}|{s.get('state')}"
            if s.get("state") in {"completed", "disconnected", "failed"} and key != announced and s.get("started_at"):
                announced = key
                self.on_event("reply", f"Meet {s['state']}. {s.get('summary', '')[:160]}")
                if s.get("state") == "completed":
                    self._speak(f"The meeting has ended, sir. {s.get('summary') or 'Notes are saved.'}")
                elif s.get("state") == "disconnected":
                    self._speak("I was disconnected from the meeting, sir. I saved the notes up to that point.")
                else:
                    self._speak(f"I couldn't stay in the meeting, sir. {s.get('detail', '')}")

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
        if last and about_the_message(transcript, last):
            prefix += (
                f"[Most recent incoming {last['app']} message from {last['who']}: \"{last['text']}\". "
                f"This is context for a reply only if {self.config.user_name} asks for one.]\n"
            )
        return f"{prefix}\n{transcript}" if prefix else transcript

    def _greeting(self) -> str:
        import time as _t

        h = _t.localtime().tm_hour
        part = "morning" if h < 12 else "afternoon" if h < 17 else "evening"
        name = self.config.user_name
        return f"Good {part}, {name}. Jarvis online — say 'Hey Jarvis' whenever you need me."

    def _status_report(self) -> str:
        """"Status report": hardware, subsystems, the research agent, unread messages."""
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
        from ..integrations.power_supply import spoken_state
        power_info = f" Power: {spoken_state()}."

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
            f"Computer stats: CPU is at {cpu_p} percent with {mem_free} gigabytes of memory free{gpu_info}.{power_info} "
            f"{subsys_msg} "
            f"{ai_msg}"
            f"{wa_msg}"
        )

        return reply

    # --- one conversation -----------------------------------------------------------------
    def _say(self, text: str, force: bool = False) -> None:
        """Show and speak a reply. Technical errors are said in a sentence, never read out."""
        self.on_event("reply", text)
        self._speak(speakable(text), force=force)

    async def _conversation_from_wake(self, agent, resumed: bool = False) -> None:
        """Woken (or back from a dictation that interrupted a conversation): listen, then keep
        the conversation going until it ends."""
        from . import voice_log

        media_was_on = self._media_on
        explicit_end = False
        try:
            if resumed:
                self.on_event("listening", "back from dictation — no wake word needed")
                transcript = self._record_transcript(wait_s=self._conversation.window_s)
                if not transcript:
                    self._preempted = False       # dictation again resumes us once more
                    return                        # otherwise: over, and `finally` closes it
            else:
                self.on_event("wake")
                self._conversation.wake()
                if media_was_on:
                    # The video keeps playing, quieter, for as long as the conversation lasts.
                    threading.Thread(target=self._media.duck, daemon=True).start()
                # Clear the tail of the wake phrase before recording, or it lands inside the
                # command. Three frames is ~240ms — enough for "…Jarvis", short enough that a
                # command spoken straight afterwards is not clipped.
                self._flush_mic(frames=3)
                transcript = self._record_transcript(wait_s=POST_WAKE_WAIT_S)
            if not transcript and self._preempted:
                # The dictation key took the microphone: no apology, straight to dictation.
                self._preempted = False
                return
            if not transcript:
                voice_log.metric("wake", reason="no_request", media=media_was_on, aec=self.aec_active)
                self.on_event("heard", "(nothing captured)")
                if media_was_on:
                    # Almost always the video saying something like "Jarvis". Apologising over
                    # the lecture for a question nobody asked is the annoying half of a false
                    # wake; going quietly back to sleep is not.
                    return
                # "I didn't catch that" sends someone off to repeat themselves, louder, at a
                # microphone that is not connected. When the device delivered pure silence,
                # say that instead, and name it.
                self._speak(getattr(self, "_capture_problem", "") or "Sorry sir, I didn't catch that.")
                return
            explicit_end = await self._turns(agent, transcript)
        finally:
            if explicit_end and hasattr(agent, "end_conversation"):
                await agent.end_conversation()
            if self._conversation.state is not ConvState.DICTATION:
                # Over (a dictation in the middle of it resumes it instead): the video's volume
                # goes back to exactly what it was.
                if self._media.ducked:
                    threading.Thread(target=self._media.restore, daemon=True).start()
                self._conversation.end()
            self.on_event("sleep")

    async def _after_barge(self, reply_text: str) -> Optional[str]:
        """The person talked over Jarvis. Record what they are saying, starting from the onset
        the monitor kept; return the transcript, or None when there was nothing to it."""
        from . import voice_log

        barge = self._take_barge()
        if barge is None:
            return None
        unsaid = self._unsaid(reply_text) if reply_text else ""
        self._conversation.interrupted(unsaid)
        record_at = time.monotonic()
        stop_ms = (barge.get("stopped_at", record_at) - barge["onset_at"]) * 1000
        voice_log.metric("barge_in", onset_to_stop_ms=stop_ms,
                         onset_to_record_ms=(record_at - barge["onset_at"]) * 1000,
                         detector_ms=barge.get("detector_ms", 0.0), policy=barge.get("policy", ""),
                         stage=barge.get("during", ""))
        self.on_event("timing", f"barge-in: onset→stop {stop_ms:.0f} ms ({barge.get('policy')})")
        return self._record_transcript(wait_s=2.0, prefix=barge["frames"])

    async def _fast_path(self, agent, transcript: str) -> Optional[str]:
        """Session and safety controls and local media keys: answered here, in the voice
        process, without a network round trip. Returns what to say ("" for nothing), or None
        when the request is not one of these."""
        from . import conversation as _conv, voice_log
        from .media import transport_command

        said = transcript.strip()
        started = time.monotonic()
        if _conv.is_cancel_all(said):
            self.stop_speaking()
            report = await agent.cancel_tasks() if hasattr(agent, "cancel_tasks") else ""
            self._conversation.stopped()
            voice_log.metric("fast_path", route="cancel_all", ms=(time.monotonic() - started) * 1000)
            return "Cancelled, sir." + (f" {report}" if report else "")
        if _conv.is_stop(said):
            # Whatever was being said or done has been cut already (that is how "stop" got
            # heard); the conversation stays open and nothing more needs saying.
            self._conversation.stopped()
            voice_log.metric("fast_path", route="stop", ms=(time.monotonic() - started) * 1000)
            return ""
        if _conv.is_continue(said) and self._conversation.interrupted_reply:
            voice_log.metric("fast_path", route="continue", ms=(time.monotonic() - started) * 1000)
            return self._conversation.take_interrupted()
        name = transport_command(said)
        if name is not None:
            self._conversation.acting(f"media.{name}")
            ok, message = await asyncio.to_thread(self._media.transport, name)
            voice_log.metric("fast_path", route=f"media.{name}", ok=ok,
                             ms=(time.monotonic() - started) * 1000)
            # Also when nothing is playing: found live, "wait, pause it" with no player went on to
            # the model, which answered with something about Pythagoras.
            return message
        return None

    async def _turns(self, agent, transcript: str) -> bool:
        """Handle requests until the conversation ends. True when it ended with a goodbye."""
        from . import voice_log
        from .speech_text import language_of

        while transcript:
            # His own voice, heard back through the microphone. From the history: "you> Give me a
            # moment." and "you> What would you like?" — both Jarvis's words — answered as requests.
            if not self._dictating and self._is_own_echo(transcript):
                voice_log.metric("ignored", stage="own_echo")
                self.on_event("timing", "ignored: my own voice")
                transcript = self._record_transcript(wait_s=self._conversation.window_s)
                continue
            # Only the name: "Hey Jarvis." A brain asked this answers "Hello… Good evening… how can
            # I assist?" every time. A plain "Yes?" and listening is what the name asks for.
            if _ONLY_THE_NAME.match(transcript.strip()):
                self.on_event("heard", transcript)
                self._say("Yes?")
                transcript = self._record_transcript(wait_s=max(8.0, self._conversation.window_s))
                continue
            self.on_event("heard", "(dictated text)" if self._dictating else transcript)

            # --- dictation: every word is typed, nothing is obeyed ---
            # Checked before anything else, because in dictation a sentence that looks
            # like a command is just a sentence someone is writing.
            from .. import dictation as _dict
            from ..commands import clean_text as _dclean
            from ..misheard import fix as _fix_heard
            # The trigger is matched against a corrected transcript — "dick tate" is how
            # "dictate" arrives — but what gets typed is what was actually said.
            _said = _fix_heard(_dclean(transcript))
            if self._dictating:
                if _dict.wants_to_stop(_said) or _dict.wants_to_stop(transcript):
                    self._dictating = False
                    self._say("Stopped dictating, sir.")
                    return False
                if _dict.is_the_trigger(_said) or _dict.is_the_trigger(transcript):
                    transcript = self._record_transcript(wait_s=max(12, self.config.follow_up_s))
                    continue
                # The same engine as the dictation key: cleanup, the field's profile,
                # verified insertion, and history — so Hindi works (ydotool could not
                # type it) and the words never go into the reply log.
                if self._flow is not None:
                    done = await asyncio.to_thread(self._flow.handle_text, transcript)
                    typed = str(done.get("status", "")).startswith(("inserted", "pasted", "typed", "preview"))
                else:
                    typed = _dict.type_out(transcript)
                self.on_event("reply" if typed else "error",
                              "Typed." if typed else "I couldn't type that — nothing has focus.")
                # Straight back to listening: dictation is continuous, and a wake word
                # between every sentence would make it useless.
                transcript = self._record_transcript(wait_s=max(12, self.config.follow_up_s))
                continue
            if _dict.wants_to_start(_said):
                self._dictating = True
                where = _dict.somewhere_to_type()
                opening = ("Dictating — say “stop dictation” when you're done."
                           if where else
                           "Dictating, but nothing on screen looks like a text box — "
                           "click where you want the words first.")
                self.on_event("reply", opening)
                self._speak(opening.split(" — ")[0] + ", sir." if where
                            else "Dictating, sir, but click into a text box first.")
                transcript = self._record_transcript(wait_s=max(12, self.config.follow_up_s))
                continue

            # --- is this for us? In the follow-up window nobody said "Jarvis", so a
            # cough, a "hmm" or a line from the TV must not become a command.
            verdict = self._conversation.judge(transcript)
            if verdict == END:
                self._say("Alright, sir.")
                return True
            if verdict == IGNORE:
                voice_log.metric("ignored", stage=self._conversation.state.value)
                self.on_event("timing", "ignored in follow-up (not a request)")
                if self._conversation.interrupted_reply:
                    # An interruption that turned out to be nothing (a cough, the TV): finish
                    # what was being said.
                    rest = self._conversation.take_interrupted()
                    self._speak(rest)
                    transcript = await self._next(agent, rest)
                    continue
                transcript = self._record_transcript(wait_s=self._conversation.window_s)
                continue
            self._conversation.language = language_of(transcript)
            self._conversation.thinking()
            started = time.monotonic()
            ended = getattr(self, "_speech_ended_at", 0.0) or started

            # --- soft on/off: while asleep, only a wake phrase gets through ---
            from .. import power as _power
            from ..commands import clean_text as _clean, _WAKE_RE, _SLEEP_RE
            _cmd = _clean(transcript).lower().rstrip(".!?")
            if _power.asleep():
                if _WAKE_RE.match(_cmd):
                    _power.set_asleep(False)
                    self._say("I'm back online, sir.", force=True)
                return False
            if _SLEEP_RE.match(_cmd):
                self._say("Going to sleep, sir. Say “Jarvis, wake up” when you need me.", force=True)
                _power.set_asleep(True)
                self._conversation.sleep()
                return True

            # --- the teaching overlay: lessons, their controls ("continue", "go back"), drawing.
            # Before the fast path, whose "stop" and "continue" mean something else while a
            # lesson is on screen: continuing a lesson redraws as well as speaks.
            taught = await self._teach_turn(transcript)
            was_taught = taught is not None
            while taught is not None:
                voice_log.metric("turn", end_to_action_ms=(time.monotonic() - ended) * 1000, route="teach")
                if self._barge is not None:
                    said = await self._after_barge("")
                    if said and self._conversation.judge(said) != IGNORE:
                        transcript = said
                        break
                    # Not the person (a cough, the TV): the lesson carries on where it stopped.
                    self._conversation.take_interrupted()
                    from ..teach import assistant as _teach
                    taught = await _teach.handle("continue", speaker=_LessonVoice(self))
                    continue
                if taught.text and not taught.spoken:
                    self._say(taught.text)
                transcript = await self._next(agent, taught.text or "")
                break
            else:
                if was_taught:
                    transcript = await self._next(agent, "")
            if was_taught:
                continue

            # --- session controls and local media keys: no network, no model ---
            quick = await self._fast_path(agent, _cmd)
            if quick is not None:
                voice_log.metric("turn", end_to_action_ms=(time.monotonic() - ended) * 1000,
                                 route="fast_path")
                if quick:
                    self._say(quick)
                transcript = await self._next(agent, quick)
                continue

            _instant = instant_intercept(_cmd)
            if _instant is not None:
                self._conversation.acting(_instant)
                if _instant == "status":
                    reply = await asyncio.to_thread(self._status_report)
                elif _instant == "mirror_phone":
                    from ..integrations import apps
                    ok, msg = apps.phone_mirror()
                    reply = "Opening your phone on screen now, sir." if ok else msg
                else:
                    from ..integrations import apps
                    ok = apps.phone_ring(self.config.kde_device_id or None)
                    reply = "Ringing your phone now, sir." if ok else "Couldn't reach your phone over KDE Connect."
                self._say(reply)
                transcript = await self._next(agent, reply)
                continue

            # Instant Coding / Antigravity Voice Intercept
            from ..integrations import coding
            v_ctx = coding.active_context()
            if wants_coding_agent(transcript, v_ctx.get("is_vscode_active", False)):
                # The shared backend owns the provider/model/effort dialogue and job.
                reply = await agent.send(transcript)
                self._say(reply)
                transcript = await self._next(agent, reply)
                continue

            # "…, then bye": do the request, then close. The goodbye is not sent on —
            # to a messaging request it is not the message, and to the model it is
            # small talk that swallows the request.
            request, closing = split_closing(transcript)
            if not closing or not request:
                request = transcript
            # One id per utterance: however it reaches the brain, it is acted on once.
            import uuid as _uuid
            event_id = _uuid.uuid4().hex
            agent.event_id = event_id
            # `handled` means the answer has already been spoken and shown, sentence by
            # sentence, while the brain was still writing it. Saying it again here would
            # repeat the whole reply aloud and print it twice.
            handled = False
            try:
                reply, handled = await self._ask_and_say(agent, self._augment(request))
            except Exception as exc:  # noqa: BLE001 - never let one bad turn kill the loop
                reply = "Something went wrong on that one, sir."
                voice_log.metric("error", kind=type(exc).__name__, stage="brain")
                self.on_event("error", f"brain: {type(exc).__name__}")
            self.on_event("timing", f"thought in {time.monotonic() - started:.1f}s")
            voice_log.metric("turn", end_to_action_ms=(time.monotonic() - ended) * 1000, route="brain",
                             streamed=handled)
            if self._barge is not None:
                # Talked over before or while answering: that is the next request.
                abandoned = not (reply or "").strip()
                transcript = await self._after_barge(reply)
                if abandoned and (not transcript
                                  or self._conversation.judge(transcript) == IGNORE):
                    # It was not the person (the TV, a cough): the request stands. Asked again
                    # with the same event id, so the backend hands back the answer to the first
                    # asking instead of doing the work twice.
                    voice_log.metric("barge_in_false", stage="thinking")
                    self._conversation.take_interrupted()
                    self._conversation.thinking()
                    agent.event_id = event_id
                    reply, handled = await self._ask_and_say(agent, self._augment(request))
                    if self._barge is not None:
                        transcript = await self._after_barge(reply)
                        continue
                    if not handled and (reply or "").strip():
                        self._say(reply)
                    transcript = await self._next(agent, reply)
                    continue
                if transcript is None:
                    transcript = self._record_transcript(wait_s=self._conversation.window_s)
                continue
            if not (reply or "").strip():
                reply = "I don't have an answer for that, sir."
                handled = False
            if not handled:
                self._say(reply)
            if not self.config.enable_followup:
                return False
            # They said goodbye after the request. Anything it created — a message held
            # for a yes — lives in the approval manager, not in this conversation, so it
            # survives the close. Only a real question ("What should I send?") keeps
            # the window open, because closing on it would strand the answer.
            if closing and not (reply or "").rstrip().endswith("?"):
                self._conversation.replied(reply)
                return True
            transcript = await self._next(agent, reply)
        return False

    async def _next(self, agent, reply: str) -> Optional[str]:
        """After Jarvis has spoken: the interruption, if there was one, or the follow-up.
        Returns the next transcript, or None when the conversation is over."""
        if self._barge is not None:
            said = await self._after_barge(reply)
            if said:
                return said
            # A barge-in that captured nothing was not the person — finish the sentence.
            rest = self._conversation.take_interrupted()
            if rest:
                self._speak(rest)
                if self._barge is not None:
                    return await self._next(agent, rest)
        if not self.config.enable_followup:
            return None
        if self._conversation.replied(reply) is not ConvState.FOLLOW_UP:
            return None
        self.on_event("listening", "follow-up — no wake word needed")
        transcript = self._record_transcript(wait_s=self._conversation.window_s)
        if not transcript and not self._preempted:
            self._conversation.silence()
        return transcript

    # --- main loop -------------------------------------------------------
    async def run(self, agent) -> None:
        """Drive the session. `agent` is a connected JarvisAgent."""
        try:
            # Whatever a previous voice process drew is nobody's lesson now.
            from ..teach.bus import overlay as _overlay
            threading.Thread(target=_overlay().control, args=("reset",), daemon=True).start()
            _overlay().listen()          # knows what is on the overlay before anyone asks
        except Exception:  # noqa: BLE001
            pass
        self.mic.start()
        self._last_message = None
        self._events: asyncio.Queue = asyncio.Queue()
        self._conversation.window_s = float(self.config.follow_up_s)
        threading.Thread(target=self._watch_media, daemon=True, name="media-state").start()
        from ..notifications import Aggregator
        self._notices = Aggregator(shared=True)
        asyncio.create_task(self._flush_notices())
        from .. import power
        power.set_asleep(False)  # a fresh voice start means Jarvis is on
        await self._setup_phone()

        from ..jobs import notify, reminders, timers  # speak timer/reminder alerts aloud in voice mode

        timers.set_announcer(self._speak)
        reminders.set_announcer(self._speak)
        notify.set_announcer(self._speak)
        asyncio.create_task(reminders.run_checker(self.config.vault_path))
        asyncio.create_task(self._watch_screen())  # proactive alerts during live screen-share
        asyncio.create_task(self._watch_afk(agent))  # welcome-back brief after a long idle gap
        asyncio.create_task(self._watch_coding_jobs())  # speak coding-job completions + chime
        asyncio.create_task(self._watch_meet())  # announce when a joined Meet ends, with summary
        asyncio.create_task(self._watch_power())  # "laptop charging, battery N%" on plug/unplug
        # Terminal scraping steals the clipboard and a quiet screen is not proof of completion.
        # Managed coding jobs provide an explicit completion state instead.
        asyncio.create_task(self._keep_study_mode())  # re-close distractions while studying
        asyncio.create_task(self._watch_coding_agents())  # process activity on the HUD only
        asyncio.create_task(self._watch_claude_stops())  # only a real Claude Stop hook speaks

        if self.config.screen_always:  # keep screen vision on from the start
            from ..vision import live

            live.set_active(True)
        try:
            # The energy threshold is only needed for the fallback endpointer and for barge-in.
            if self.threshold <= 0:
                self.on_event("calibrating")
                self.threshold = vad.calibrate_threshold(self.mic.read)
            if self.backend == "local":
                from . import local_stt, local_tts

                self.on_event("loading", "warming up speech models…")
                local_stt.warmup(
                    self.config.whisper_model,
                    self.config.partial_model if self.config.live_partials else None,
                )
                # Load whichever voice will speak, now rather than on the first reply —
                # about a second for Kokoro, 1.6 s for Piper, paid while the screen still
                # says it is warming up.
                local_tts.warmup(self.config.piper_model)
                # "Done, sir", "Give me a moment": synthesised once, in the background, so the
                # lines said most often never wait on the model.
                threading.Thread(target=local_tts.warm_acknowledgements,
                                 args=(self.config.piper_model,), daemon=True).start()
                if self.config.live_partials and self._endpointer is not None:
                    self._partials = local_stt.PartialTranscriber(
                        on_text=lambda t: self.on_event("partial", "…" if self._dictating else t),
                        sample_rate=self.sample_rate,
                        model_name=self.config.partial_model,
                        vocabulary=self.config.stt_vocabulary,
                    )
            self.on_event(
                "loading",
                f"endpointing: {'silero (neural)' if self._endpointer else 'energy threshold'}"
                f" · stt: {self.config.whisper_model} beam {self.config.whisper_beam}",
            )
            # Push-to-talk: a key press starts a turn without the wake word. Started here rather
            # than in __init__ so a failure to read the keyboard cannot stop the session existing.
            self._ptt = hotkey.start(self.config.ptt_key, self._ptt_pressed.set)
            if self._ptt is not None:
                self.on_event("loading", f"push-to-talk: {self.config.ptt_key}")
            elif hotkey.resolve_key(self.config.ptt_key) is not None:
                self.on_event("loading", f"push-to-talk unavailable — {hotkey.diagnose(self.config.ptt_key)}")

            try:
                self._start_dictation_keys()
            except Exception as exc:  # noqa: BLE001 — dictation missing must not stop the assistant
                self.on_event("loading", f"dictation unavailable ({type(exc).__name__})")

            phrase = "Hey Jarvis" if self.backend == "local" else self.config.wake_keyword
            hint = f"wake word: '{phrase}'"
            if self._ptt is not None:
                hint += f"  ·  or press {self.config.ptt_key}"
            self.on_event("ready", hint)
            # Once per login, not on every restart: restarted after every change, "Good evening,
            # sir. Jarvis online" was heard again and again. The marker lives in the runtime
            # directory, which is emptied at logout and reboot.
            marker = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "jarvis-greeted"
            if not marker.exists():
                self._speak(self._greeting())
                try:
                    marker.touch()
                except OSError:
                    pass
            while True:
                try:
                    kind, payload = await self._wait_for_wake_or_event()
                    if kind == "event":
                        await self._handle_phone_event(payload, agent)
                        continue
                    await self._conversation_from_wake(agent, resumed=(kind == "resume"))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — a failed turn must not end the service
                    self._conversation.error(type(exc).__name__)
                    from . import voice_log
                    voice_log.metric("error", kind=type(exc).__name__, stage="turn")
                    self.on_event("error", f"voice turn failed ({type(exc).__name__})")
                    self._media.restore()
                    self._conversation.recover()
                    self.on_event("sleep")
        finally:
            self.mic.stop()
            self.mic.delete()
            self.wake.delete()


ECHO_WINDOW_S = 12.0
_ONLY_THE_NAME = re.compile(
    r"(?i)^(?:(?:hey|hi|hello|ok|okay|yo)[\s,]+)?(?:jarvis|javis|jarvi|jarves|jarviz|jars|jervis|travis|h\.?r\.?i\.?s)[\s.!?,]*$")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9ऀ-ॿ']+", (text or "").lower())


_REPLY_WORDS = re.compile(
    r"(?i)\b(?:repl(?:y|ies)|respond|answer\s+(?:him|her|them)|tell\s+(?:him|her|them)|text\s+(?:him|her|them|back)|"
    r"message\s+(?:him|her|them|back)|write\s+back|say\s+back|what\s+did\s+(?:he|she|they)\s+(?:say|send|write)|"
    r"(?:his|her|their|the|that|last)\s+(?:message|text)|jawab|reply\s+kar|usko|use\s+bol|unko|bol\s+do|likh\s+do)\b|"
    r"जवाब|उसे|उसको|उनको|बोल\s+दो|लिख\s+दो")
MESSAGE_CONTEXT_S = 600.0


def about_the_message(transcript: str, last: dict, now: Optional[float] = None) -> bool:
    """Whether a spoken turn is about the last incoming message.

    It used to be attached to every turn for as long as the process lived. Found in the history:
    "Is Claude completed?" was answered "I'll send that message to the unknown number…", and a
    sentence it did not understand got "use whatsapp_send to reply" — the model read the note as
    the request. Now it goes with a turn only when the message is recent and the turn is about
    replying or names the sender."""
    if not last or (now or time.time()) - float(last.get("at") or 0) > MESSAGE_CONTEXT_S:
        return False
    said = transcript or ""
    if _REPLY_WORDS.search(said):
        return True
    first = re.split(r"\s+", str(last.get("who") or "").strip())[0]
    return len(first) >= 3 and re.search(rf"(?i)\b{re.escape(first)}\b", said) is not None


class _LessonVoice:
    """The voice session as a teaching-overlay speaker (teach.runner.Speaker)."""

    def __init__(self, session: "VoiceSession") -> None:
        self.session = session

    def speak(self, phrases, on_start):
        return self.session._speak_phrases(list(phrases), on_start)

    def stop(self) -> None:
        self.session.stop_speaking()
