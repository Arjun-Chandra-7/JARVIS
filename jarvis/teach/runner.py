"""Running a lesson: each phrase spoken, each picture drawn as its words are heard.

The runner owns one lesson at a time. It speaks through a ``Speaker`` — the voice session's,
or a clock for typed requests — and a phrase's overlay commands go out from the speaker's
``on_start`` callback, stamped with the moment that phrase becomes audible. The renderer draws
them at that moment and reports how far off it was; that number is the measured drift.

Interruptions:
  * Barge-in (or "pause") stops the voice; the runner freezes every animation in place and
    remembers the phrase it was on.
  * "Continue" cancels whatever that phrase had half-drawn and says the phrase again, so the
    picture and the words line up from there.
  * "Go back" restores the picture exactly as it was before the previous step — a snapshot from
    the scene mirror, drawn instantly — and teaches that step again.
  * A new lesson, "clear", or an emergency dismissal bumps the overlay generation: anything the
    old lesson still had in flight is dropped by the renderer, and callbacks from its speech are
    ignored here, because they carry a run token that no longer matches.
"""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from .bus import Overlay, overlay as default_overlay
from .plan import LessonPlan, Step
from .scene import SceneModel, clear as clear_cmd, still


@dataclass
class Spoken:
    started: int          # phrases whose audio began
    finished: int         # phrases played to their end
    interrupted: bool


class Speaker(Protocol):
    def speak(self, phrases: list[str], on_start: Callable[[int, float], None]) -> Spoken: ...

    def stop(self) -> None: ...


class ClockSpeaker:
    """For typed requests and tests: no audio, each phrase given the time it would take to say.

    The pictures still arrive phrase by phrase, so a typed "explain RAG with a diagram" draws the
    same way; only the words are read rather than heard.
    """

    def __init__(self, words_per_s: float = 2.6, sleep: Callable[[float], None] = time.sleep) -> None:
        self.words_per_s = words_per_s
        self.sleep = sleep
        self._stop = threading.Event()

    def speak(self, phrases, on_start):
        self._stop.clear()
        for i, text in enumerate(phrases):
            if self._stop.is_set():
                return Spoken(i, i, True)
            on_start(i, time.time() * 1000 + 30)
            duration = max(0.7, len(text.split()) / self.words_per_s)
            end = time.monotonic() + duration
            while time.monotonic() < end:
                if self._stop.is_set():
                    return Spoken(i + 1, i, True)
                self.sleep(min(0.05, end - time.monotonic()) if end > time.monotonic() else 0)
        return Spoken(len(phrases), len(phrases), False)

    def stop(self):
        self._stop.set()


def instant(cmds: list) -> list:
    """The same commands without their animation — for catching up on skipped phrases."""
    out = []
    for c in cmds:
        c = copy.deepcopy(c)
        if c["op"] in ("shape.add", "stroke.draw"):
            c["object"] = still(c["object"])
        elif c["op"] == "highlight.show":
            c["pulse"] = False
        out.append(c)
    return out


class LessonRunner:
    IDLE, PLAYING, PAUSED, DONE = "idle", "playing", "paused", "done"

    def __init__(self, overlay: Optional[Overlay] = None) -> None:
        self.overlay = overlay or default_overlay()
        self.lock = threading.RLock()
        self.lesson: Optional[LessonPlan] = None
        self.scene = SceneModel()
        self.status = self.IDLE
        self.pos = (0, 0)                     # (step, phrase) to speak next
        self.current_step = 0                 # the step most recently begun
        self.snapshots: dict[int, dict] = {}  # step index → the picture before it
        self.keep = False
        self.token = 0                        # changes whenever a run is superseded
        self.speaker: Optional[Speaker] = None
        self.last_activity = 0.0
        self._cleanup: Optional[threading.Timer] = None
        self._unsubscribe = None
        self.log: list[dict] = []             # numbers only: what happened when, for the report

    # ------------------------------------------------------------------ state
    @property
    def active(self) -> bool:
        return self.lesson is not None and self.status != self.IDLE

    def _note(self, what: str, **kw) -> None:
        self.log.append({"t": round(time.time(), 3), "what": what, **kw})
        del self.log[:-200]

    def _send(self, cmds: list, at: Optional[float] = None) -> None:
        if not cmds:
            return
        self.scene.apply(cmds)
        self.overlay.send(cmds, at=at)

    def _watch(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self.overlay.on_event(self._on_overlay_event)

    def _on_overlay_event(self, evt: dict) -> None:
        kind = evt.get("type")
        if kind == "dismissed":
            # The emergency shortcut: the overlay is already empty; stop talking about it.
            with self.lock:
                if self.speaker:
                    self.speaker.stop()
                self._reset(send=False)
                self._note("dismissed", by=evt.get("by"))
        elif kind == "renderer_gone" and self.active:
            # A fresh renderer knows nothing. Put the current picture back on it, still.
            with self.lock:
                self.overlay.send(self.scene.restore_cmds(self.scene.snapshot()))
                self._note("restored_after_crash")
        elif kind == "display_changed" and self.active:
            with self.lock:
                if self.speaker:
                    self.speaker.stop()
                self._reset(send=False)
                self._note("display_changed")

    # ------------------------------------------------------------------ playing
    def start(self, lesson: LessonPlan, speaker: Speaker) -> Spoken:
        with self.lock:
            self._cancel_cleanup()
            if self.speaker and self.status == self.PLAYING:
                self.speaker.stop()
            self.token += 1
            self.lesson = lesson
            self.scene = SceneModel()
            self.snapshots = {}
            self.keep = False
            self.pos = (0, 0)
            self.current_step = 0
            self.overlay.new_generation(lesson.lesson_id)
            self._watch()
            self._send(lesson.setup)
            self._note("start", topic=lesson.topic, language=lesson.language)
        return self.play(speaker)

    def _sequence(self, frm: tuple[int, int]) -> list[tuple[int, int]]:
        assert self.lesson is not None
        seq = []
        for si in range(frm[0], len(self.lesson.steps)):
            for pi in range(frm[1] if si == frm[0] else 0, len(self.lesson.steps[si].phrases)):
                seq.append((si, pi))
        return seq

    def play(self, speaker: Speaker, frm: Optional[tuple[int, int]] = None) -> Spoken:
        """Speak from ``frm`` (default: where the lesson is) to the end, or until interrupted."""
        with self.lock:
            if self.lesson is None:
                return Spoken(0, 0, False)
            self._cancel_cleanup()
            self.token += 1
            token = self.token
            self.speaker = speaker
            seq = self._sequence(frm or self.pos)
            steps = self.lesson.steps
            texts = [steps[s].phrases[p].text for s, p in seq]
            self.status = self.PLAYING
            self.last_activity = time.time()

        def on_start(i: int, at_ms: float) -> None:
            with self.lock:
                if token != self.token or i >= len(seq):
                    return                      # a superseded run's speech: not ours any more
                s, p = seq[i]
                if p == 0 or s not in self.snapshots:
                    self.snapshots.setdefault(s, self.scene.snapshot())
                self.current_step = s
                self.pos = (s, p)
                self._send(steps[s].phrases[p].visuals, at=at_ms)

        if not texts:
            return self._finished(Spoken(0, 0, False), token, seq)
        try:
            result = speaker.speak(texts, on_start)
        except Exception:  # noqa: BLE001 — half a picture is worse than none
            with self.lock:
                if token == self.token:
                    self._send([{"op": "timeline.cancel"}])
                    self.status = self.PAUSED
            raise
        return self._finished(result, token, seq)

    def _finished(self, result: Spoken, token: int, seq: list) -> Spoken:
        with self.lock:
            if token != self.token:
                return result
            self.last_activity = time.time()
            if result.interrupted:
                # Frozen where it stood. The phrase that was cut off is the one to say again.
                idx = max(0, min(result.started - 1, len(seq) - 1)) if seq else 0
                if result.finished >= result.started and result.started < len(seq):
                    idx = result.started           # cut between phrases: carry on with the next
                self.pos = seq[idx] if seq else self.pos
                self.status = self.PAUSED
                self._send([{"op": "timeline.pause"}])
                self._note("paused", step=self.pos[0], phrase=self.pos[1])
            else:
                self.pos = (len(self.lesson.steps), 0) if self.lesson else (0, 0)
                self.status = self.DONE
                self._note("done")
                self._schedule_cleanup()
            return result

    # ------------------------------------------------------------------ controls
    def pause(self) -> bool:
        with self.lock:
            if self.status != self.PLAYING:
                return False
            if self.speaker:
                self.speaker.stop()
            return True

    def resume(self, speaker: Speaker) -> Optional[Spoken]:
        with self.lock:
            if self.lesson is None or self.status not in (self.PAUSED, self.DONE):
                return None
            if self.status == self.DONE:
                return None
            s, p = self.pos
            # Whatever the cut-off step had half-drawn goes; what its earlier phrases drew comes
            # back finished, and the cut-off phrase draws its part again as it is said.
            done_before = [c for ph in self.lesson.steps[s].phrases[:p] for c in ph.visuals] if s < len(self.lesson.steps) else []
            self._send([{"op": "timeline.cancel", "timeline": self._timeline(s)}] + instant(done_before)
                       + [{"op": "timeline.resume"}])
        return self.play(speaker)

    def _timeline(self, step_index: int) -> str:
        assert self.lesson is not None
        prefix = "rag-" if self.lesson.topic == "rag" else "step-"
        return f"{prefix}{self.lesson.steps[step_index].id}" if step_index < len(self.lesson.steps) else prefix + "x"

    def back(self, speaker: Speaker) -> Optional[Spoken]:
        """Teach the previous step again, from the picture as it was before it."""
        with self.lock:
            if self.lesson is None:
                return None
            if self.speaker and self.status == self.PLAYING:
                self.speaker.stop()
            here = min(self.current_step, len(self.lesson.steps) - 1)
            target = max(0, here - 1)
        return self._replay_from(target, speaker)

    def again(self, speaker: Speaker) -> Optional[Spoken]:
        """This step once more, from its beginning."""
        with self.lock:
            if self.lesson is None:
                return None
            if self.speaker and self.status == self.PLAYING:
                self.speaker.stop()
            target = min(self.current_step, len(self.lesson.steps) - 1)
        return self._replay_from(target, speaker)

    def _replay_from(self, step_index: int, speaker: Speaker) -> Spoken:
        # Never speak while holding the lock: a dismissal has to be able to get in mid-sentence.
        with self.lock:
            snap = self.snapshots.get(step_index)
            self.token += 1                        # the interrupted run's callbacks are now void
            self.overlay.new_generation(self.lesson.lesson_id)
            if snap is not None:
                cmds = self.scene.restore_cmds(snap)
                self.scene.apply(cmds)
                self.overlay.send(cmds)
            # Later snapshots describe a future that is about to be redrawn.
            self.snapshots = {k: v for k, v in self.snapshots.items() if k <= step_index}
            self.pos = (step_index, 0)
            self.current_step = step_index
            self._note("replay", step=step_index)
        return self.play(speaker, (step_index, 0))

    def skip(self, speaker: Speaker) -> Optional[Spoken]:
        """Finish this step's pictures at once, and teach the next one."""
        with self.lock:
            if self.lesson is None:
                return None
            if self.speaker and self.status == self.PLAYING:
                self.speaker.stop()
            s, p = self.pos
            rest = [c for ph in self.lesson.steps[s].phrases[p:] for c in ph.visuals] if s < len(self.lesson.steps) else []
            self.token += 1
            self._send(instant(rest) + [{"op": "timeline.resume"}])
            if s + 1 >= len(self.lesson.steps):
                self.pos = (len(self.lesson.steps), 0)
                self.status = self.DONE
                self._schedule_cleanup()
                return Spoken(0, 0, False)
            self.pos = (s + 1, 0)
        return self.play(speaker, (s + 1, 0))

    def follow_up(self, step: Step, speaker: Speaker) -> Spoken:
        """Say and draw something extra on the lesson already there, without moving its place."""
        with self.lock:
            self._cancel_cleanup()
            if self.speaker and self.status == self.PLAYING:
                self.speaker.stop()
            self.token += 1
            token = self.token
            was = self.status
            before = self.pos
            self.scene.checkpoint()
            self.status = self.PLAYING
            self.speaker = speaker
            phrases = step.phrases
            self._send([{"op": "timeline.resume"}])

        def on_start(i: int, at_ms: float) -> None:
            with self.lock:
                if token == self.token and i < len(phrases):
                    self._send(phrases[i].visuals, at=at_ms)

        result = speaker.speak([p.text for p in phrases], on_start)
        with self.lock:
            if token == self.token:
                self.pos = before
                self.status = self.PAUSED if (result.interrupted or was == self.PAUSED) else self.DONE
                if result.interrupted:
                    self._send([{"op": "timeline.pause"}])
                elif self.status == self.DONE:
                    self._schedule_cleanup()
                self.last_activity = time.time()
                self._note("follow_up", step=step.id, interrupted=result.interrupted)
        return result

    def draw(self, cmds: list, checkpoint: bool = True) -> None:
        """A one-off drawing ("draw a circle", "highlight this"), undoable."""
        with self.lock:
            self._cancel_cleanup()
            if checkpoint:
                self.scene.checkpoint()
            if self.lesson is None and self.scene.empty():
                self.overlay.new_generation("draw")
            self._watch()
            self._send(cmds)
            if self.status == self.IDLE:
                self.status = self.DONE
            self.keep = True                  # a drawing the person asked for stays until cleared

    def undo(self) -> bool:
        with self.lock:
            cmds = self.scene.undo()
            if cmds is None:
                return False
            self.overlay.send(cmds)
            return True

    def redo(self) -> bool:
        with self.lock:
            cmds = self.scene.redo()
            if cmds is None:
                return False
            self.overlay.send(cmds)
            return True

    def leave(self) -> None:
        with self.lock:
            self.keep = True
            self._cancel_cleanup()

    def clear(self, group: Optional[str] = None) -> None:
        with self.lock:
            if group:
                self.scene.checkpoint()
                self._send([clear_cmd(group)])
                return
            if self.speaker and self.status == self.PLAYING:
                self.speaker.stop()
            self._reset(send=True)

    def _reset(self, send: bool) -> None:
        self.token += 1
        self._cancel_cleanup()
        lesson_id = self.lesson.lesson_id if self.lesson else "idle"
        self.overlay.new_generation(lesson_id)
        if send:
            self.overlay.send([clear_cmd()])
        self.lesson = None
        self.scene = SceneModel()
        self.snapshots = {}
        self.status = self.IDLE
        self.pos = (0, 0)
        self.keep = False
        # Nothing the lesson read from the screen outlives it.
        self._note("cleared")

    # ------------------------------------------------------------------ cleanup
    def _schedule_cleanup(self) -> None:
        self._cancel_cleanup()
        if self.keep or self.lesson is None:
            return
        delay = float(self.lesson.cleanup_policy.get("auto_clear_s", 14.0))
        token = self.token

        def go():
            with self.lock:
                if token == self.token and not self.keep and self.status == self.DONE:
                    self._reset(send=True)
        self._cleanup = threading.Timer(delay, go)
        self._cleanup.daemon = True
        self._cleanup.start()

    def _cancel_cleanup(self) -> None:
        if self._cleanup is not None:
            self._cleanup.cancel()
            self._cleanup = None


RUNNER = LessonRunner()


def runner() -> LessonRunner:
    return RUNNER
