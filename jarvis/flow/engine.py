"""From the key press to text in the field.

    key down ─▶ capture (the voice loop hands over the microphone) ─▶ key up / tap / Escape
             ─▶ focused field read (in parallel with) speech to text
             ─▶ command?  "new paragraph", "make this formal", "always spell this as…"
             ─▶ text:     cleanup for the field's profile ─▶ insert, verified ─▶ history

Terminal text is shown first and waits for the key to be pressed again; Escape drops it.
Changes to the dictionary wait for the same confirmation. Nothing here ever runs a command,
presses Enter, or sends a message.

Events go to the overlay as ``emit(state, **details)``; the transcript itself is never part of
an event except the terminal preview, which has to be seen to be confirmed.
"""
from __future__ import annotations

import array
import inspect
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import clipboard, commands, dictionary, history, profiles
from . import cleanup as _cleanup
from . import insert as _insert
from . import stt as _stt
from .focus import BRIDGE

MIN_SPEECH_S = 0.25
MAX_CAPTURE_S = float(os.environ.get("JARVIS_DICTATION_MAX_S", "120"))
PENDING_S = 15.0


def _rms(frame) -> float:
    if not frame:
        return 0.0
    return math.sqrt(sum(s * s for s in frame) / len(frame)) / 32768.0


class DictationEngine:
    def __init__(self, emit: Optional[Callable] = None, *, bridge=BRIDGE, stt_fn=_stt.transcribe,
                 inserter=_insert.insert, clip=clipboard, complete=None, clock=time.monotonic) -> None:
        self._emit = emit or (lambda state, **k: None)
        self.bridge, self.stt_fn, self.inserter, self.clip = bridge, stt_fn, inserter, clip
        self.complete = complete
        self.clock = clock
        self.pending: Optional[dict] = None
        self.last: Optional[dict] = None
        self.metrics: dict = {}
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._focus_future = None
        self._clip_future = None
        self._lock = threading.Lock()

    def emit(self, state: str, **details) -> None:
        try:
            self._emit(state, **details)
        except Exception:  # noqa: BLE001 — a missing overlay must not break dictation
            pass

    # ------------------------------------------------------------------ capture
    def capture(self, read: Callable[[], list], stop: threading.Event, cancel: threading.Event,
                *, rate: int = 16000, key_at: Optional[float] = None, max_s: float = MAX_CAPTURE_S,
                speech_level: float = 0.012, preroll=()) -> Optional[bytes]:
        """Frames until the key says stop. None when cancelled. Audio stays in memory.

        ``preroll``: frames heard just before the key went down, so a first word started a
        moment early is not clipped."""
        self.metrics = {}                     # one session's numbers, never the last one's
        pcm = array.array("h")
        for frame in preroll:
            pcm.extend(frame)
        self.metrics["preroll_ms"] = round(len(pcm) / rate * 1000)
        started = self.clock()
        # Where the words will go is read now, while the person speaks: it decides how Hindi is
        # written before the recogniser runs, and costs nothing after the key comes up.
        self._focus_future = self._pool.submit(self.bridge.request, "focus")
        spoke, last_level = any(_rms(f) > speech_level for f in preroll), 0.0
        first = True
        while not stop.is_set() and not cancel.is_set():
            frame = read()
            if first:
                first = False
                self.metrics["key_to_recording_ms"] = round(((self.clock() - key_at) if key_at else 0) * 1000, 1)
                self.emit("listening")
            pcm.extend(frame)
            level = _rms(frame)
            if not spoke and level > speech_level:
                spoke = True
                self.emit("speech")
            now = self.clock()
            if now - last_level > 0.1:
                last_level = now
                self.emit("level", level=round(min(1.0, level * 12), 2))
            if now - started > max_s:
                self.emit("timeout")
                break
        if cancel.is_set():
            self.emit("cancelled")
            return None
        self.metrics["captured_s"] = round(len(pcm) / rate, 2)
        self.metrics["spoke"] = spoke
        return pcm.tobytes() if spoke else b""

    # ------------------------------------------------------------------ after the key comes up
    def finish(self, pcm: Optional[bytes], *, rate: int = 16000) -> dict:
        released = self.clock()
        if pcm is None:
            return {"status": "cancelled"}
        if len(pcm) / 2 / rate < MIN_SPEECH_S:
            self.emit("idle", message="Didn't hear anything")
            return {"status": "empty"}
        self.emit("processing")
        # Reading the clipboard costs 100–170 ms; done now, while speech is recognised, so a paste
        # does not wait for it. Unused (and dropped) when the text goes in through accessibility.
        self._clip_future = self._pool.submit(self.clip.save) if hasattr(self.clip, "save") else None
        focus = getattr(self, "_focus_future", None) or self._pool.submit(self.bridge.request, "focus")
        self._focus_future = None
        try:
            info = focus.result(timeout=2)
        except Exception:  # noqa: BLE001
            info = {"ok": False}
        if info.get("ok") and self._target(info).is_secret:
            # A password field: the audio is not even sent to be transcribed.
            self.emit("refused", message="Password field — not typing there")
            return {"status": "refused"}
        entries = dictionary.load()
        profile = profiles.profile_for(self._target(info)) if info.get("ok") else "prose"
        prompt = _stt.prompt_for(dictionary.vocabulary(entries), _stt.hindi_script(profile), profile)
        heard = self.stt_fn(pcm, rate, prompt)
        del pcm                                                    # the audio is gone now
        self.metrics["release_to_transcript_s"] = round(self.clock() - released, 2)
        self.metrics["stt_provider"] = heard.provider
        if not heard.text:
            offline = heard.failures and all("ConnectError" in f or "Timeout" in f or "no Groq key" in f
                                             for f in heard.failures if not f.startswith("local"))
            self.emit("offline" if offline and not heard.text else "error",
                      message="Couldn't make out any words" if not heard.failures or "empty" in " ".join(heard.failures)
                      else "Speech recognition failed")
            return {"status": "no_text", "failures": heard.failures}
        return self.handle_text(heard.text, info, entries=entries)

    # ------------------------------------------------------------------ text or command
    @staticmethod
    def _target(info: dict) -> profiles.Target:
        return profiles.Target(app=info.get("app", ""), window=info.get("window", ""), role=info.get("role", ""),
                               name=info.get("name", ""), editable=bool(info.get("editable", True)),
                               multi_line=bool(info.get("multi_line")), secret=bool(info.get("secret")))

    def handle_text(self, raw: str, info: Optional[dict] = None, *, entries=None) -> dict:
        info = info if info is not None else self.bridge.request("focus")
        target = self._target(info)
        if info.get("ok") and target.is_secret:
            self.emit("refused", message="Password field — not typing there")
            return {"status": "refused"}             # nothing kept: no history for secret fields
        command = commands.parse(raw)
        if command is not None:
            return self.run_command(command, info, raw)
        profile = profiles.profile_for(target) if info.get("ok") else "prose"
        entries = entries if entries is not None else dictionary.load()
        t0 = self.clock()
        cleaned = _cleanup.clean(raw, profiles.CLEANUP_PROFILE[profile], dictionary.replacements(entries))
        self.metrics["cleanup_ms"] = round((self.clock() - t0) * 1000, 1)
        text = self._join(cleaned.text, info, profile)
        if not text.strip():
            self.emit("idle", message="Nothing left to type")
            return {"status": "empty"}
        if profile == "terminal":
            row = history.add(app=target.app, profile=profile, raw=raw, cleaned=text, status="preview")
            self.pending = {"kind": "terminal", "text": text, "raw": raw, "row": row, "app": target.app,
                            "expires": self.clock() + PENDING_S}
            self.emit("preview", preview=text, message="Press the dictation key to insert · Esc to discard")
            return {"status": "preview", "text": text}
        return self._put(text, raw, info, profile, target.app)

    def _join(self, text: str, info: dict, profile: str) -> str:
        """A space before the new words when the cursor sits right after a word."""
        before = info.get("before") or ""
        if not before and (info.get("length") or 0) > 0 and self.last and self.last.get("text"):
            # The field has text it will not show (GTK 4 reads back as empty): if what sits
            # before the cursor is what was just dictated, that is what the space depends on.
            before = self.last["text"]
        if before and not before[-1].isspace() and text[:1].isalnum() and profile not in {"code", "search"}:
            return " " + text
        return text

    def _put(self, text: str, raw: str, info: dict, profile: str, app: str, row: Optional[str] = None) -> dict:
        if os.environ.get("JARVIS_DICTATION_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}:
            # Everything up to the field, and nothing into it: for trying dictation safely.
            self.metrics["insert_ms"] = 0.0
            history.add(app=app, profile=profile, raw=raw, cleaned=text.strip(), status="dry_run")
            self.emit("inserted_unverified", message="Dry run — nothing was typed")
            return {"status": "dry_run", "text": text, "profile": profile}
        self.emit("inserting")
        t0 = self.clock()
        extra = {}
        saved, self._clip_future = getattr(self, "_clip_future", None), None
        if saved is not None and "saved" in inspect.signature(self.inserter).parameters:
            extra["saved"] = saved
        done = self.inserter(text, info, terminal=profile == "terminal", **extra)
        self.metrics["insert_ms"] = round((self.clock() - t0) * 1000, 1)     # including read-back
        self.metrics["insert_sent_ms"] = getattr(done, "sent_ms", -1.0)      # until the text was delivered
        status = done.status if done.verified or done.status in {"refused", "failed"} else f"{done.status}_unverified"
        if row:
            history.update(row, status=status, reason=done.reason, method=done.method)
        else:
            row = history.add(app=app, profile=profile, raw=raw, cleaned=text.strip(), status=status,
                              reason=done.reason, method=done.method)
        if done.landed:
            self.last = {"text": text, "start": done.start, "end": done.end, "verified": done.verified,
                         "method": done.method, "row": row, "profile": profile}
        if done.status == "refused":
            self.emit("refused", message=done.reason)
        elif done.status == "failed":
            self.emit("error", message=f"{done.reason}. Saved — say “paste last dictation” to try again")
        elif done.verified:
            self.emit("inserted")
        else:
            self.emit("inserted_unverified", message=done.reason or "Sent, but couldn't confirm")
        return {"status": status, "method": done.method, "text": text, "reason": done.reason}

    # ------------------------------------------------------------------ confirmations
    def confirm_pending(self) -> dict:
        with self._lock:
            pending, self.pending = self.pending, None
        if not pending:
            return {"status": "none"}
        if self.clock() > pending["expires"]:
            self.emit("cancelled", message="That preview expired")
            return {"status": "expired"}
        if pending["kind"] == "terminal":
            info = self.bridge.request("focus")
            return self._put(pending["text"], pending["raw"], info, "terminal", pending["app"], pending.get("row"))
        if pending["kind"] == "dictionary":
            dictionary.save(pending["entries"])
            self.emit("inserted", message=pending["done"])
            return {"status": "saved"}
        return {"status": "none"}

    def discard_pending(self) -> None:
        with self._lock:
            pending, self.pending = self.pending, None
        if pending and pending.get("row"):
            history.update(pending["row"], status="discarded")
        self.emit("cancelled")

    # ------------------------------------------------------------------ commands
    def run_command(self, cmd: commands.Command, info: dict, raw: str) -> dict:
        kind = cmd.kind
        self.emit("inserting", message=kind.replace("_", " "))
        if kind == "cancel":
            self.emit("cancelled")
            return {"status": "cancelled"}
        if kind in {"new_line", "new_paragraph"}:
            if profiles.profile_for(profiles.Target(app=info.get("app", ""), role=info.get("role", ""))) == "terminal":
                self.emit("refused", message="No new lines in a terminal")
                return {"status": "refused"}
            return self._put("\n\n" if kind == "new_paragraph" else "\n", raw, info, "document", info.get("app", ""))
        if kind == "undo":
            from ..integrations import desktop_control
            ok = desktop_control.press_keys("ctrl+z")
            self.emit("inserted_unverified" if ok else "error", message="Undo sent" if ok else "Couldn't send undo")
            return {"status": "undo_sent" if ok else "failed"}
        if kind == "clear_history":
            n = history.clear()
            self.last = None
            self.emit("inserted", message=f"Cleared {n} dictation{'s' if n != 1 else ''}")
            return {"status": "cleared", "count": n}
        if kind == "discard_last":
            ok = history.discard_last()
            self.emit("inserted" if ok else "idle", message="Discarded" if ok else "Nothing to discard")
            return {"status": "discarded" if ok else "none"}
        if kind in {"copy_last", "paste_last", "retry"}:
            row = history.last()
            if not row:
                self.emit("error", message="No dictation to use")
                return {"status": "none"}
            if kind == "copy_last":
                ok = self.clip.put(row["cleaned"])
                self.emit("inserted" if ok else "error", message="Copied" if ok else "Couldn't copy")
                return {"status": "copied" if ok else "failed"}
            if kind == "retry" and row.get("status") in {"inserted", "pasted"}:
                self.emit("idle", message="That one already went in")
                return {"status": "already_inserted"}
            return self._put(row["cleaned"], row.get("raw", ""), info, row.get("profile", "prose"), info.get("app", ""))
        if kind in {"always_spell", "add_to_dictionary", "forget_correction"}:
            return self._propose_dictionary(kind, cmd.args, info)
        # The rest edit what was just dictated (or what is selected).
        target, where = self._edit_target(info)
        if not target:
            self.emit("error", message="Nothing to edit — select text or dictate first")
            return {"status": "no_target"}
        if kind in {"bullet_list", "numbered_list"}:
            return self._replace(where, target, commands.as_bullets(target, kind == "numbered_list"), info)
        if kind == "replace":
            old, new = cmd.args["old"], cmd.args["new"]
            idx = target.lower().rfind(old.lower())
            if idx < 0:
                self.emit("error", message=f"Couldn't find “{old}”")
                return {"status": "not_found"}
            return self._replace(where, target, target[:idx] + new + target[idx + len(old):], info)
        if kind in {"delete_last_sentence", "delete_last_word", "select_last_sentence"}:
            pieces = re.split(r"(?<=[.!?।])\s+", target.rstrip()) if "sentence" in kind else target.rstrip().split(" ")
            keep = (" " if "word" in kind else " ").join(pieces[:-1])
            if kind == "select_last_sentence":
                if where and where[0] >= 0:
                    s = where[0] + len(target.rstrip()) - len(pieces[-1])
                    ok = self.bridge.request("select", start=s, end=where[0] + len(target.rstrip())).get("ok")
                    self.emit("inserted" if ok else "error", message="Selected" if ok else "Couldn't select here")
                    return {"status": "selected" if ok else "failed"}
                self.emit("error", message="Can't select in this app")
                return {"status": "failed"}
            if kind == "delete_last_word" and not where:
                from ..integrations import desktop_control
                ok = desktop_control.press_keys("ctrl+backspace")
                self.emit("inserted_unverified" if ok else "error", message="Deleted a word" if ok else "Couldn't delete")
                return {"status": "deleted_unverified" if ok else "failed"}
            return self._replace(where, target, keep, info)
        if kind == "transform":
            new = self._transform(target, cmd.args.get("style", "grammar"))
            if not new:
                self.emit("error", message="No model available for that edit right now")
                return {"status": "no_model"}
            return self._replace(where, target, new, info)
        return {"status": "unknown"}

    def _edit_target(self, info: dict) -> tuple[str, Optional[tuple[int, int]]]:
        """(the text to edit, its range in the field if it can be addressed)."""
        if info.get("selected") and info.get("selection"):
            s, e = info["selection"]
            return info["selected"], (s, e)
        last = self.last
        if last and last.get("text", "").strip():
            text = last["text"]
            if last.get("verified") and last.get("start", -1) >= 0:
                now = self.bridge.request("read", start=last["start"], end=last["end"])
                if now.get("ok") and now.get("text") == text:
                    return text, (last["start"], last["end"])
            return text, None
        return "", None

    def _replace(self, where: Optional[tuple[int, int]], old: str, new: str, info: dict) -> dict:
        if where and where[0] >= 0:
            s, e = where
            if self.bridge.request("select", start=s, end=e).get("ok"):
                got = self.bridge.request("insert", text=new, replace_selection=True)
                if got.get("verified") or got.get("ok"):
                    if self.last is not None:
                        self.last.update(text=new, start=s, end=s + len(new), verified=bool(got.get("verified")))
                    self.emit("inserted" if got.get("verified") else "inserted_unverified")
                    return {"status": "edited", "text": new}
        # Can't address the text in this app: hand the new version over rather than guess.
        self.clip.put(new)
        self.emit("inserted_unverified", message="Can't edit in place here — the new version is on your clipboard")
        return {"status": "copied", "text": new}

    def _transform(self, text: str, style: str) -> str:
        prompt = commands.TRANSFORM_PROMPTS.get(style)
        if not prompt:
            return ""
        complete = self.complete
        if complete is None:
            from ..llm import complete_sync
            from ..config import CONFIG

            def complete(system, user):
                done = complete_sync(system, user, CONFIG, temperature=0.2, timeout=12.0, strength="strong")
                return done.text if done.ok else ""
        system = (prompt + " Return only the rewritten text, nothing else — no quotes, no preface.")
        return (complete(system, text) or "").strip().strip('"')

    def _propose_dictionary(self, kind: str, args: dict, info: dict) -> dict:
        entries = dictionary.load()
        if kind == "always_spell":
            written = args["written"]
            source = info.get("selected") or (self.last or {}).get("text", "")
            spoken = dictionary.closest_word(source, written) or ""
            new = dictionary.with_spelling(entries, written, spoken)
            done = f"Saved: “{spoken or written}” → {written}"
        elif kind == "add_to_dictionary":
            phrase = (info.get("selected") or (self.last or {}).get("text", "")).strip(" .,!?।")
            if not phrase or len(phrase.split()) > 4:
                self.emit("error", message="Select the name first, then say it again")
                return {"status": "no_target"}
            new = dictionary.with_spelling(entries, phrase, "")
            done = f"Added {phrase}"
        else:
            new = dictionary.without_last_added(entries)
            if len(new) == len(entries):
                self.emit("error", message="No correction to forget")
                return {"status": "none"}
            done = "Forgot the last correction"
        self.pending = {"kind": "dictionary", "entries": new, "done": done, "expires": self.clock() + PENDING_S}
        self.emit("confirm", message=f"{done.replace('Saved:', 'Save').replace('Added', 'Add')}? "
                                     "Press the dictation key to confirm · Esc to cancel")
        return {"status": "confirm"}
