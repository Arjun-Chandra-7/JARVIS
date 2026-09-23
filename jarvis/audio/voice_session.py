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
        self.mic = Microphone(self.wake.frame_length, device_index=device)
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

    # --- stages ----------------------------------------------------------
    async def _wait_for_wake_or_event(self):
        """Block until the wake word fires, push-to-talk is pressed, or a phone event is queued.

        Returns ('wake'|'event', payload).
        """
        while True:
            if self._ptt_pressed.is_set():
                # The caller emits "wake" for every path, so do not emit it again here — doing so
                # logged two wakes for one key press and looked like a double trigger.
                self._ptt_pressed.clear()
                return ("wake", None)
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
            "id": event.get("id"), "repliable": event.get("repliable"),
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

    def _record_transcript(self, wait_s: float) -> Optional[str]:
        capture_start = time.monotonic()
        speech_end = [0.0]

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
                self.mic.read,
                sample_rate=self.sample_rate,
                frame_length=self.frame_length,
                silence_ms=self.config.endpoint_hangover_ms,
                max_s=self.config.max_utterance_s,
                wait_s=wait_s,
                vad=self._endpointer,
                on_speech_start=lambda: self.on_event("listening"),
                on_partial=on_partial if partial is not None else None,
                on_level=watch_level,
                partial_every_ms=self.config.partial_every_ms,
            )
            speech_end[0] = time.monotonic()
            if partial is not None:
                partial.cancel()
        else:
            pcm = vad.record_utterance(
                self.mic.read,
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
        self.on_event("timing",
                      f"heard {duration:.1f}s clip, endpoint+capture {speech_end[0]-capture_start:.1f}s, "
                      f"transcribed in {stt_s:.2f}s "
                      f"-> {'\"'+text+'\"' if text else 'EMPTY (STT found no words)'}")
        if text:
            self.on_event("transcript", text)   # committed transcript replaces any partial
        return text

    def _transcribe(self, pcm: bytes) -> str:
        if self.backend == "local":
            from . import local_stt

            return local_stt.transcribe(
                pcm, self.sample_rate, self.config.whisper_model, self.config.whisper_beam,
                self.config.stt_language, self.config.stt_vocabulary,
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

    def _speak(self, text: str, force: bool = False) -> None:
        text = self._clean_for_speech(text)
        if not text.strip():
            return

        def play(stop, on_first_audio, on_level):
            if self.backend == "local":
                from . import local_tts

                local_tts.speak(
                    text, self.config.piper_model, self.config.audio_output_device,
                    stop_event=stop, on_first_audio=on_first_audio, on_level=on_level,
                )
            else:
                tts.speak(
                    text, self.config.elevenlabs_api_key, self.config.tts_voice_id,
                    self.config.tts_model_id, self.sample_rate,
                    self.config.audio_output_device, stop_event=stop,
                )

        self._while_speaking(play, announce=text, force=force)

    async def _ask_and_say(self, agent, prompt: str) -> tuple[str, bool]:
        """Ask the brain and speak the answer while it is still being written.

        Returns (reply, handled). `handled` says the answer has already been spoken and shown,
        sentence by sentence — the caller must then do neither, or the whole reply is repeated
        aloud and printed twice.

        The two run at once: the brain fills a queue with fragments and the speaking thread
        drains it, so the first sentence is heard while the last is still being generated. What
        is returned is the finished reply, because everything downstream — the HUD, the journal,
        the follow-up — still wants the whole thing.

        Falls back to waiting for the reply whenever streaming is not available, which is not a
        rare path: the hosted voice takes whole utterances, and a provider can refuse to stream.
        """
        if self.backend != "local":
            return await agent.send(prompt), False

        fragments: "queue.Queue[Optional[str]]" = queue.Queue()

        def drain():
            while True:
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
            self.on_event("reply", sentence)

        speaker = threading.Thread(
            target=lambda: spoken.append(
                self._speak_as_written(drain(), on_sentence=show)), daemon=True)

        agent.on_reply_delta = fragments.put
        try:
            speaker.start()
            reply = await agent.send(prompt)
        finally:
            agent.on_reply_delta = None
            fragments.put(None)          # closes the stream whether it finished or failed
            speaker.join(timeout=30)

        # What was actually said, versus what the brain ended up returning. They differ when a
        # tool ran: the loop speaks nothing for those rounds and the reply is assembled
        # afterwards, so there is still something left to say.
        already = "".join(spoken).strip()
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
                on_sentence=on_sentence,
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
        monitor: Optional[threading.Thread] = None
        if self.config.enable_barge_in:
            monitor = threading.Thread(target=self._barge_in_monitor, args=(stop,), daemon=True)
            monitor.start()
        self._speaking.set()
        speech_control.set_speaking(True)
        # A stop requested before this utterance started must not silence it.
        stop_token = speech_control.token()
        watcher = threading.Thread(
            target=self._watch_stop_request, args=(stop, stop_token), daemon=True
        )
        watcher.start()
        try:
            play(
                stop,
                lambda: self.on_event("speaking", announce) if announce is not None
                else self.on_event("speaking"),
                lambda lvl: levels.publish(lvl, "speaking"),
            )
        finally:
            stop.set()
            self._speaking.clear()
            speech_control.set_speaking(False)
            levels.publish(0.0, "")     # idle: stop the HUD animating a level nothing is producing
            if monitor is not None:
                monitor.join(timeout=1.0)
            self._flush_mic()
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
                self.mic.read()
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
        self._conversation = ConversationSession(window_s=float(self.config.follow_up_s))
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
                if self.config.live_partials and self._endpointer is not None:
                    self._partials = local_stt.PartialTranscriber(
                        on_text=lambda t: self.on_event("partial", t),
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

            phrase = "Hey Jarvis" if self.backend == "local" else self.config.wake_keyword
            hint = f"wake word: '{phrase}'"
            if self._ptt is not None:
                hint += f"  ·  or press {self.config.ptt_key}"
            self.on_event("ready", hint)
            self._speak(self._greeting())  # Jarvis speaks first
            while True:
                kind, payload = await self._wait_for_wake_or_event()
                if kind == "event":
                    await self._handle_phone_event(payload, agent)
                    continue
                self.on_event("wake")
                self._conversation.wake()
                # Clear the tail of the wake phrase before recording, or it lands inside the
                # command. Three frames is ~240ms — enough for "…Jarvis", short enough that a
                # command spoken straight afterwards is not clipped.
                self._flush_mic(frames=3)
                transcript = self._record_transcript(wait_s=POST_WAKE_WAIT_S)
                if not transcript:
                    # Woke but captured nothing intelligible → say so instead of going silent, so it
                    # never looks "stuck". Usually a mic/VAD/threshold issue (see the timing log).
                    self.on_event("heard", "(nothing captured)")
                    # "I didn't catch that" sends someone off to repeat themselves, louder, at a
                    # microphone that is not connected. When the device delivered pure silence,
                    # say that instead, and name it.
                    self._speak(getattr(self, "_capture_problem", "")
                                or "Sorry sir, I didn't catch that.")
                    self.on_event("sleep")
                    continue
                while transcript:
                    self.on_event("heard", transcript)
                    t_lower = transcript.lower().strip()

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
                            self.on_event("reply", "Stopped dictating, sir.")
                            self._speak("Stopped dictating, sir.")
                            self.on_event("sleep")
                            break
                        # The phrase that started this must never end up in the document. It
                        # arrives again more often than it should — the tail of the same sentence
                        # landing in the next capture, or the user repeating it because nothing
                        # visibly happened — and "jarvis dictate mode" typed into your notes is
                        # the one thing you were certainly not dictating.
                        if _dict.is_the_trigger(_said) or _dict.is_the_trigger(transcript):
                            transcript = self._record_transcript(
                                wait_s=max(12, self.config.follow_up_s))
                            continue
                        typed = _dict.type_out(transcript)
                        self.on_event("reply" if typed else "error",
                                      _dict.as_typed(transcript) if typed
                                      else "I couldn't type that — nothing has focus.")
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
                        self.on_event("reply", "Alright.")
                        self._speak("Alright, sir.")
                        break
                    if verdict == IGNORE:
                        self.on_event("timing", f"ignored in follow-up: {transcript!r}")
                        transcript = self._record_transcript(wait_s=self._conversation.window_s)
                        continue
                    self._conversation.thinking()

                    # --- soft on/off: while asleep, only a wake phrase gets through ---
                    from .. import power as _power
                    from ..commands import clean_text as _clean, _WAKE_RE, _SLEEP_RE
                    _cmd = _clean(transcript).lower().rstrip(".!?")
                    if _power.asleep():
                        if _WAKE_RE.match(_cmd):
                            _power.set_asleep(False)
                            self.on_event("reply", "I'm back online, sir.")
                            self._speak("I'm back online, sir.", force=True)
                        # anything else: stay asleep, stay silent
                        self.on_event("sleep")
                        break
                    if _SLEEP_RE.match(_cmd):
                        msg = "Going to sleep, sir. Say “Jarvis, wake up” when you need me."
                        self.on_event("reply", msg)
                        self._speak(msg, force=True)
                        _power.set_asleep(True)
                        self.on_event("sleep")
                        break

                    _instant = instant_intercept(_cmd)
                    # Executive Wake Briefing: "wake up", "status report", "system pulse", "subsystems"
                    if _instant == "status":
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
                        
                        self.on_event("reply", reply)
                        self._speak(reply)
                        break

                    # Instant Phone Command Intercept: "open my phone", "open phone", "mirror phone", "show my phone"
                    if _instant == "mirror_phone":
                        from ..integrations import apps
                        ok, msg = apps.phone_mirror()
                        reply = "Opening your phone on screen now, sir." if ok else msg
                        self.on_event("reply", reply)
                        self._speak(reply)
                        break

                    # Instant Ring Phone Intercept: "ring my phone", "find my phone", "where is my phone"
                    if _instant == "ring_phone":
                        from ..integrations import apps
                        ok = apps.phone_ring(self.config.kde_device_id or None)
                        reply = "Ringing your phone now, sir." if ok else "Couldn't reach your phone over KDE Connect."
                        self.on_event("reply", reply)
                        self._speak(reply)
                        break

                    # Instant Coding / Antigravity Voice Intercept
                    from ..integrations import coding
                    v_ctx = coding.active_context()
                    is_vs_active = v_ctx.get("is_vscode_active", False)

                    if wants_coding_agent(transcript, is_vs_active):
                        # The shared backend owns the provider/model/effort dialogue and job.
                        reply = await agent.send(transcript)
                        self.on_event("reply", reply)
                        self._speak(reply)
                        transcript = self._record_transcript(wait_s=max(12, self.config.follow_up_s))
                        continue

                    start = time.monotonic()
                    # "…, then bye": do the request, then close. The goodbye is not sent on —
                    # to a messaging request it is not the message, and to the model it is
                    # small talk that swallows the request.
                    request, closing = split_closing(transcript)
                    if not closing or not request:
                        request = transcript
                    # One id per utterance: however it reaches the brain, it is acted on once.
                    import uuid as _uuid
                    agent.event_id = _uuid.uuid4().hex
                    # `handled` means the answer has already been spoken and shown, sentence by
                    # sentence, while the brain was still writing it. Saying it again here would
                    # repeat the whole reply aloud and print it twice.
                    handled = False
                    try:
                        reply, handled = await self._ask_and_say(
                            agent, self._augment(request))
                    except Exception as exc:  # noqa: BLE001 - never let one bad turn kill the loop
                        reply = "Something went wrong on that one, sir."
                        self.on_event("error", f"brain: {exc}")
                    self.on_event("timing", f"thought in {time.monotonic() - start:.1f}s")
                    if not (reply or "").strip():
                        reply = "I don't have an answer for that, sir."
                        handled = False
                    if not handled:
                        self.on_event("reply", reply)
                        self._speak(reply)
                    # By default require the wake word again for each turn (no auto-listen after a
                    # reply). Set JARVIS_FOLLOWUP=1 to keep the conversational follow-up window.
                    if not self.config.enable_followup:
                        break
                    if self._conversation.replied(reply) is not ConvState.FOLLOW_UP_WINDOW:
                        break
                    # They said goodbye after the request. Anything it created — a message held
                    # for a yes — lives in the approval manager, not in this conversation, so it
                    # survives the close. Only a real question ("What should I send?") keeps
                    # the window open, because closing on it would strand the answer.
                    if closing and not (reply or "").rstrip().endswith("?"):
                        break
                    self.on_event("listening", "follow-up — no wake word needed")
                    self._flush_mic(frames=3)   # the tail of our own voice, not the person
                    transcript = self._record_transcript(wait_s=self._conversation.window_s)
                self._conversation.end()
                self.on_event("sleep")
        finally:
            self.mic.stop()
            self.mic.delete()
            self.wake.delete()
