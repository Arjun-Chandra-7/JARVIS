"""The 3D Studio session: one project, one Blender, one worker thread.

Everything that touches Blender runs on the studio's single worker thread, so reconstruction
never blocks a conversation turn and two requests never race inside Blender. A turn waits a
few seconds for a quick edit to finish; anything longer carries on in the background and is
announced when it is done.

Pipeline for a new model:

  capture → analyse → (ask, if a critical fact is missing) → build → refine → materials →
  compare → save → present in Blender

and for an edit:

  parse → resolve parts → check locks → autosave → apply to the model → rebuild only the
  affected parts → verify Blender (sizes, unrelated geometry untouched) → checkpoint, or roll
  back to the last version.
"""
from __future__ import annotations

import concurrent.futures as cf
import copy
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import (brain_routes, capture, editing, exports, jobs, planner, privacy, resources, scene_compiler,
               validation)
from . import parametric_reconstruction as pr
from .blender_bridge import BlenderSession, BridgeError
from .project import Project, projects_root
from .types import (ErrorCategory, Evidence, Fidelity, JobState, Mode, ReferenceAsset, StudioModel,
                    ValidationResult, ViewKind)

QUICK_WAIT_S = 12.0
MIN_PLAUSIBLE_IOU = 0.6       # below this the capture, not the model, is what went wrong


@dataclass
class StudioConfig:
    present: bool = True                 # open the result in a Blender window
    watch: bool = False                  # build in the visible window ("show me while you make it")
    root: Optional[str] = None
    max_iterations: int = 8
    memory_mb: int = 4096


def _default_announce(text: str, speak: bool = False) -> None:
    try:
        from ..jobs.notify import notify
        notify("3D Studio", text, speak=speak, category="job")
    except Exception:  # noqa: BLE001
        pass


def _default_status(text: str) -> None:
    try:
        from ..teach.bus import overlay
        overlay().transport.post("studio", text)
    except Exception:  # noqa: BLE001
        pass


class Studio:
    def __init__(self, cfg: Optional[StudioConfig] = None, *, announce: Callable = _default_announce,
                 status: Callable[[str], None] = _default_status, book: Optional[jobs.JobBook] = None,
                 grab: Optional[Callable] = None, ocr: Optional[Callable] = None,
                 window: Callable[[], tuple] = lambda: ("", "")) -> None:
        self.cfg = cfg or StudioConfig()
        self.root = Path(self.cfg.root) if self.cfg.root else projects_root()
        self.announce = announce
        self.status_line = status
        self.book = book or jobs.JobBook()
        self.grab, self.ocr, self.window = grab, ocr, window
        self.pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-3d")
        self.session: Optional[BlenderSession] = None
        self.project: Optional[Project] = None
        self.model: Optional[StudioModel] = None
        self.job = None
        self.control = jobs.Control()
        self.refs: list[ReferenceAsset] = []
        self.known: dict = {}
        self.unit: Optional[str] = None
        self.allow_estimate = False
        self.pending: Optional[editing.Clarify] = None
        self.last_active = 0.0
        self.facts: dict = {}
        self.metrics: dict = {}
        self._lock = threading.RLock()

    # ================================================================ public surface
    @property
    def active(self) -> bool:
        return self.project is not None or (self.job is not None and not self.job.state.terminal)

    def recently_used(self, within_s: float = 1800) -> bool:
        return self.active and time.time() - self.last_active < within_s

    def start(self, request: str, *, how: str = "auto", paths: Optional[list] = None,
              view: ViewKind = ViewKind.UNKNOWN, region=None, wait: bool = False,
              known: Optional[dict] = None) -> str:
        """A new model from the screen (default) or named image files."""
        with self._lock:
            if self.job is not None and not self.job.state.terminal and self.job.state != JobState.NEEDS_INPUT:
                return f"I'm still working on the current model ({jobs.label(self.job.state).lower()})."
            self._reset_for_new()
            self.known = dict(known or {})
            self.job = self.book.create(request)
            self.control = jobs.Control()
            self.last_active = time.time()
        fut = self.pool.submit(self._pipeline, request, how, paths or [], view, region)
        if wait:
            return fut.result()
        return "On it — I'll capture the reference and build the model in the background."

    def add_reference(self, *, how: str = "auto", path: Optional[str] = None, view: ViewKind = ViewKind.UNKNOWN,
                      region=None, wait: bool = True) -> str:
        """"This is the side view", "use this image as the front view", "take another reference"."""
        self.last_active = time.time()
        fut = self.pool.submit(self._add_reference, how, path, view, region)
        return fut.result() if wait else "Capturing the new view."

    def tell(self, text: str, wait: bool = True) -> Optional[str]:
        """A fact or answer: a dimension, units, "just estimate it", a choice for a pending question."""
        self.last_active = time.time()
        t = text.lower()
        if self.pending is not None:
            return self.edit(text, wait=wait)
        from . import calibration
        changed = False
        if re.search(r"\bjust estimate\b|\bestimate it\b|\bgood enough\b|\bdoesn'?t need to be exact\b", t):
            self.allow_estimate = True
            changed = True
        unit = re.search(r"\b(millimet\w*|mm|centimet\w*|cm|inch(?:es)?)\b", t)
        known = calibration.parse_known_dimension(t)
        if known and known[1] and known[2] is not None:
            self.known[known[0] if known[0] != "size" else "width"] = known[1]
            changed = True
        elif unit and not known:
            u = unit.group(1)
            self.unit = "mm" if u.startswith("mil") or u == "mm" else "cm" if u.startswith("c") else "in"
            changed = True
        if re.search(r"\b(?:for|to)\s+(?:3d\s+)?print", t):
            if self.job:
                self.job.purpose = "print"
            changed = True
        if not changed:
            return None
        if self.job is not None and self.job.state == JobState.NEEDS_INPUT:
            fut = self.pool.submit(self._reconstruct, self.job.request)
            return fut.result(timeout=300) if wait else "Thanks — continuing."
        return "Noted."

    def status(self) -> str:
        job = self.job
        if job is None:
            return "There's no 3D model in progress."
        if job.state == JobState.NEEDS_INPUT:
            return job.question
        if job.state == JobState.FAILED:
            return f"The last model failed safely ({job.error.value.replace('_', ' ')}). {job.stage_note}".strip()
        base = jobs.label(job.state)
        done = ", ".join(job.stages_done[-3:])
        if job.state == JobState.READY and self.project:
            where = "Ready in Blender" if getattr(self, "presented", False) else "Ready (saved, not open in a window)"
            return f"{where} — version {self.project.current}. {self._honest_line()}"
        return f"{base}." + (f" Done so far: {done}." if done else "")

    def cancel(self) -> str:
        if self.job is None or self.job.state.terminal:
            return "There's nothing to cancel."
        self.control.cancel.set()
        self.control.resume.set()
        if self.session:
            self.session.cancel()
        return "Cancelling the 3D model. Anything already saved stays as it was."

    def pause(self) -> str:
        if self.job is None or self.job.state.terminal:
            return "There's nothing running to pause."
        self.control.resume.clear()
        self._set_state(JobState.PAUSED, "paused by you")
        return "Paused. Say resume when you want me to carry on."

    def resume(self) -> str:
        if self.job is None or self.job.state != JobState.PAUSED:
            return "Nothing is paused."
        self.control.resume.set()
        return "Resuming the model."

    def delete_references(self) -> str:
        n = 0
        if self.project:
            n += privacy.References(self.project.path).delete_all()
        for r in self.refs:
            if r.path and os.path.exists(r.path):
                n += privacy.shred(r.path)
            r.path, r.deleted = "", True
        if self.session and self.session.alive() and self.project:
            try:
                scene = self.session.scene()
                ops = [{"op": "delete_object", "target": o["name"]} for o in scene["objects"] if o["role"] == "reference"]
                if ops:
                    self.session.apply(ops)
                    self.session.save(str(self.project.blend), checkpoint=False)
            except BridgeError:
                pass
        privacy.audit("refs.deleted", count=n, job=self.job.id if self.job else None)
        return f"Deleted {n} reference file{'s' if n != 1 else ''}." if n else "There were no reference files left."

    def edit(self, text: str, wait: bool = True) -> Optional[str]:
        """A voice edit on the current model. None when the words are not an edit."""
        if self.model is None or self.project is None:
            return None
        if self.pending is not None:
            op = self._answer_pending(text)
            if op is None:
                return None
        else:
            try:
                op = editing.parse(text, self.model)
            except editing.Refused as exc:
                return str(exc)
            if op is None:
                return None
        self.last_active = time.time()
        if isinstance(op, editing.Clarify):
            self.pending = op
            self.pool.submit(self._highlight, op.candidates)
            return op.question
        brain_routes.route("edit", purpose="3d.edit")      # an edit of the active job: local engines only
        fut = self.pool.submit(self._edit, op, text)
        if not wait:
            return "Working on it."
        try:
            return fut.result(timeout=QUICK_WAIT_S)
        except cf.TimeoutError:
            fut.add_done_callback(lambda f: self.announce(f.result() if not f.exception() else "That edit failed.", True))
            return "That will take a moment — I'll tell you when it's done."

    def run_generative(self, provider, ref: ReferenceAsset, approved_for: str) -> str:
        """Runs on the worker, only from an approved action. The mesh replaces the body part."""
        from . import generative
        from .types import Material, ModelPart
        privacy.audit("provider.upload", job=self.job.id if self.job else None, provider=provider.name)
        try:
            verts, faces = generative.generate_remote(provider, ref.path, approved_for=approved_for)
        except generative.ApprovalRequired as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 — the provider's failure, reported by kind only
            privacy.audit("provider.failed", provider=provider.name, error=type(exc).__name__)
            return f"{provider.name} didn't return a usable mesh."
        session = self._session()
        existing = [o["name"] for o in session.scene()["objects"]]
        m = copy.deepcopy(self.model) if self.model else StudioModel(name="Model")
        base = ModelPart(name="Generated body", geometry="mesh", params={"vertices": verts, "faces": faces,
                         "evidence": {"shape": Evidence.ESTIMATED.value, "hidden": Evidence.INVENTED.value},
                         "provider": provider.name}, evidence=Evidence.INVENTED, confidence=0.4,
                         aliases=["body", "generated"])
        m.parts = [p for p in m.parts if p.name != base.name] + [base]
        out = editing.Outcome(m, added=[base.name], message=f"Added a base mesh from {provider.name}; its hidden "
                                                           "surfaces are invented by the model.")
        return self._apply_outcome(session, editing.EditOperation(target=[], operation="generate"), out,
                                   "generated base mesh")

    def close(self) -> None:
        def _stop():
            if self.session:
                self.session.close()
                self.session = None
        try:
            self.pool.submit(_stop).result(timeout=30)
        except Exception:  # noqa: BLE001
            pass
        self.pool.shutdown(wait=False)

    # ================================================================ internals: jobs
    def _reset_for_new(self) -> None:
        # A new model is a new project: its versions and undo history are its own.
        self.refs, self.known, self.unit, self.allow_estimate = [], {}, None, False
        self.pending, self.model, self.facts, self.metrics = None, None, {}, {}
        self.project, self.presented = None, False

    def _set_state(self, state: JobState, note: str = "") -> None:
        job = self.job
        if job is None:
            return
        if job.state not in (state, JobState.PAUSED) and job.state not in (JobState.QUEUED,):
            job.stages_done.append(jobs.label(job.state).lower())
        job.state = state
        job.stage_note = note
        self.book.save(job)
        self.status_line(f"{jobs.label(state)}" + (f" — {note}" if note and state != JobState.PAUSED else ""))
        privacy.audit("job.state", job=job.id, state=state.value)

    def _fail(self, category: ErrorCategory, say: str) -> str:
        if self.job:
            self.job.error = category
            self._set_state(JobState.FAILED, say)
        privacy.audit("job.failed", job=self.job.id if self.job else None, error=category.value)
        msg = f"I couldn't finish the model: {say}" + (" The last saved version is intact." if self.project and
                                                         self.project.current else "")
        self.announce(msg, True)
        return msg

    def _ensure_project(self, name: str) -> Project:
        if self.project is None:
            self.project = Project.create(name, self.job.id if self.job else "", root=self.root)
            self.job.project_path = str(self.project.path)
        return self.project

    def _refs_store(self) -> privacy.References:
        return privacy.References(self._ensure_project("Model").path)

    def _capture(self, how: str, paths: list, view: ViewKind, region) -> list[ReferenceAsset]:
        store = self._refs_store()
        if paths:
            out = []
            for p in paths:
                p, v = (p if isinstance(p, tuple) else (p, view))      # (path, view) names each file's view
                if str(p).lower().endswith((".mp4", ".webm", ".mkv", ".mov")):
                    out += capture.video_frames(p, store)
                else:
                    out.append(capture.from_file(p, store, v))
            return out
        if how == "clipboard":
            return [capture.from_clipboard(store, view)]
        return [capture.capture_screen(store, region=region, how=how, view=view, window=self.window(),
                                       grab=self.grab, ocr_words=self.ocr)]

    def _add_reference(self, how, path, view, region) -> str:
        try:
            refs = self._capture(how, [path] if path else [], view, region)
        except capture.CaptureRefused as exc:
            return str(exc)
        except capture.CaptureFailed as exc:
            return str(exc)
        for r in refs:
            r.view = view if view != ViewKind.UNKNOWN else r.view
        self.refs += refs
        privacy.audit("refs.added", count=len(refs), view=view.value, job=self.job.id if self.job else None)
        if self.job and self.job.state in (JobState.NEEDS_INPUT, JobState.READY, JobState.FAILED):
            return self._reconstruct(self.job.request)
        return f"Got the {view.value} view." if view != ViewKind.UNKNOWN else "Got it."

    def _pipeline(self, request, how, paths, view, region) -> str:
        try:
            ok, why = resources.can_start_blender(str(self.root))
            if not ok:
                return self._fail(ErrorCategory.RESOURCE_LIMIT, f"there isn't enough room to run Blender — {why}.")
            self._ensure_project(_name_from(request))
            self._set_state(JobState.CAPTURING)
            try:
                self.refs = self._capture(how, paths, view, region)
            except capture.CaptureRefused as exc:
                self.job.error = ErrorCategory.SENSITIVE_CAPTURE
                self._set_state(JobState.FAILED, exc.verdict.category)
                return str(exc)
            except capture.CaptureFailed as exc:
                self.job.error = ErrorCategory.CAPTURE_FAILED
                self._set_state(JobState.FAILED, "capture failed")
                return str(exc)
            self.job.references = self.refs
            if not self.refs:
                return self._fail(ErrorCategory.CAPTURE_FAILED, "no reference could be read.")
            self.announce("I've captured the reference. I'm reconstructing the main geometry now.", False)
            return self._reconstruct(request)
        except jobs.Cancelled:
            return self._cancelled()
        except Exception as exc:  # noqa: BLE001 — a failure is reported, never allowed to take JARVIS down
            privacy.audit("job.error", job=self.job.id if self.job else None, error=type(exc).__name__)
            return self._fail(ErrorCategory.INTERNAL, "something unexpected went wrong while building.")

    def _cancelled(self) -> str:
        if self.job:
            self.job.error = ErrorCategory.CANCELLED
            self._set_state(JobState.CANCELLED, "cancelled")
        for r in self.refs:
            if r.retention.value == "transient" and r.path:
                privacy.shred(r.path)
        return "Cancelled."

    def _reconstruct(self, request: str) -> str:
        job = self.job
        try:
            self.control.checkpoint()
            self._set_state(JobState.ANALYSING)
            analysis = planner.analyse(self.refs, request, **({"ocr": self.ocr} if self.ocr else {}))
            self.control.checkpoint()
            name = _name_from(request)
            p = planner.plan(self.refs, request, analysis, known=self.known, unit=self.unit, name=name,
                             image_roots=[str(self.root)], allow_estimate=self.allow_estimate)
            job.mode = p.mode
            # The Daily Brain decides the route: this mode's capabilities are served by local
            # engines, so the decision is "engine" and nothing leaves the machine. Anything else
            # would mean a cloud model was chosen for a screen reference — refuse, don't proceed.
            decision = brain_routes.route(p.mode)
            privacy.audit("brain.route", job=job.id, mode=p.mode.value, provider=decision.engine or decision.route)
            if decision.route != "engine":
                raise RuntimeError("the Daily Brain did not route this reconstruction to a local engine")
            job.requested_accuracy = "exact" if analysis.classification.exact_requested else "visual"
            job.purpose = job.purpose or analysis.classification.purpose
            if p.question:
                job.question, job.missing_evidence = p.question, p.missing
                self._set_state(JobState.NEEDS_INPUT, "needs another view or a measurement")
                self.announce(p.question, True)
                return p.question
            model = p.model
            self.facts = p.facts
            self.control.checkpoint()
            self._set_state(JobState.BUILDING, f"{len(model.parts)} parts")
            session = self._session(watch=self.cfg.watch)
            session.call("new_project")
            image_paths = {r.id: r.path for r in self.refs if r.path and not r.deleted}
            ops = scene_compiler.build(model, refs=self.refs[:4], image_paths=image_paths)
            session.apply(ops, known=[], timeout=180)
            self.model = model
            self.control.checkpoint()
            self._set_state(JobState.REFINING)
            before, after, reason = self._refine(p.mode)
            job.validation = [*before, *after]
            if after and validation.worst(after).silhouette_iou < MIN_PLAUSIBLE_IOU:
                # Whatever was captured is not one object this could model — most likely the wrong
                # part of the screen. Don't call it ready; don't keep the capture.
                for r in self.refs:
                    if r.source in ("auto", "region", "window") and r.path:
                        privacy.shred(r.path)
                        r.path, r.deleted = "", True
                crop = next((r.crop for r in self.refs if r.crop), None)
                where = f" (I used a {crop.width}×{crop.height} area of the screen)" if crop else ""
                q = (f"What I captured doesn't look like one object I can model — it matches only "
                     f"{validation.worst(after).silhouette_iou * 100:.0f}%{where}. Show me the area to use, or put "
                     "the picture on a plain background, and ask again.")
                job.question, job.missing_evidence = q, ["a clear view of the object"]
                self.model = None
                self._set_state(JobState.NEEDS_INPUT, "capture did not isolate one object")
                self.announce(q, True)
                return q
            self._set_state(JobState.MATERIALS, f"{len(model.materials)} material(s)")
            self.control.checkpoint()
            self._set_state(JobState.COMPARING)
            job.fidelity = self.model.fidelity.value
            job.evidence = self.model.evidence_summary()
            silhouettes = self._silhouettes()
            n = self.project.checkpoint(session, self.model, "initial reconstruction", "build",
                                        [p.name for p in self.model.parts], silhouettes)
            job.version = n
            self.metrics = {"before": [r.to_dict() for r in before], "after": [r.to_dict() for r in after],
                            "stop": reason}
            self.pending = None
            # Present first: "ready" must mean the window is showing it, not that it soon will.
            presented = self.presented = self._present()
            self._set_state(JobState.READY, f"version {n}")
            msg = self._ready_message(presented, after, reason)
            self.announce(msg, True)
            return msg
        except jobs.Cancelled:
            return self._cancelled()
        except BridgeError as exc:
            return self._fail(exc.category, _bridge_words(exc))
        except capture.CaptureFailed as exc:
            return self._fail(ErrorCategory.CAPTURE_FAILED, str(exc))

    def _session(self, watch: bool = False) -> BlenderSession:
        if self.session is not None and self.session.alive():
            return self.session
        if self.session is not None:
            # It died. Start again from the last good checkpoint, and say so with the next reply.
            if self.project and self.project.current:
                self.session.last_checkpoint = str(self.project.blend)
            self.session.restart()
            if self.project and self.project.current:
                self.model = self.project.load_model()
                self._recovered = (f"Blender had stopped, so I restarted it from version {self.project.current} "
                                   "— nothing saved was lost. ")
            return self.session
        self.session = BlenderSession([str(self.root)], background=not watch, memory_mb=self.cfg.memory_mb,
                                      owner=self.job.id if self.job else "jarvis").start()
        return self.session

    # ================================================================ refinement
    def _views(self) -> list[tuple[str, np.ndarray, float, str]]:
        """(view, reference mask, mm/px, alignment) for every reference that has a comparable camera."""
        f = self.facts
        out = []
        if "views" in f:                                          # dimensioned drawings
            for v, vp in f["views"].items():
                out.append((v, vp.silhouette, vp.scale, "measured"))
        elif "mask" in f and self.job.mode == Mode.VECTOR:
            out.append(("front", f["mask"], f["scale_mm_per_px"], "measured"))
        elif "mask" in f:
            out.append(("perspective", f["mask"], f["scale_mm_per_px"], "shape"))
        elif "masks" in f:
            for m, a in zip(f["masks"], f["angles"]):
                out.append((f"orbit:{a}", validation._crop(m), 0.0, "shape"))
        return out

    def _measure(self) -> list[ValidationResult]:
        snap = self.session.snapshot()
        results = []
        cam = self.model.cameras[0] if self.model.cameras else None
        for view, ref, s, align in self._views():
            mm = s or (max(self.model.parts[0].dimensions()) / max(ref.shape))
            mask, _, ext = validation.model_silhouette(snap["tris"], "perspective" if view == "perspective" else view,
                                                       mm_per_px=mm, camera=cam)
            expected = None
            if view in ("front", "side", "top") and "views" in self.facts:
                expected = self.facts["views"][view].size_mm
            r = validation.compare(ref, mask, view=view, align=align, mm_per_px=mm, expected_mm=expected,
                                   model_mm=ext).result
            results.append(r)
        return results

    def _refine(self, mode: Mode) -> tuple[list, list, str]:
        """Measure; adjust the worst-matching part; measure again — until good, flat, or out of budget."""
        policy = validation.LoopPolicy(max_iterations=self.cfg.max_iterations)
        if self.facts.get("kind") == "revolve" and self.model.cameras:
            self._calibrate_camera()
        first = self._measure()
        history = [validation.worst(first).silhouette_iou] if first else []
        current = first
        reason = ""
        it = 0
        while current:
            self.control.checkpoint()
            dim_err = max(r.dimension_error_mm for r in current)
            reason = validation.stop_reason(history, policy, dim_error_mm=dim_err)
            if reason:
                break
            if self.facts.get("kind") != "revolve":
                # Drawings and logos are built directly from measurements; there is no parameter to
                # nudge towards the picture without departing from those measurements.
                reason = ("dimensions within tolerance; remaining silhouette difference is line-drawing detail"
                          if dim_err <= policy.target_dim_mm and "views" in self.facts
                          else "measured; no adjustable parameter explains the remaining difference")
                break
            it += 1
            part = self.model.parts[0]
            ref = self.facts["mask"]
            snap = self.session.snapshot()
            mm = self.facts["scale_mm_per_px"]
            ren, _, _ = validation.model_silhouette(snap["tris"], "perspective", mm_per_px=mm,
                                                    camera=self.model.cameras[0] if self.model.cameras else None)
            candidate = copy.deepcopy(self.model)
            pr.refine_revolve(candidate.parts[0], ref, validation._crop(ren))
            existing = [o["name"] for o in self.session.scene()["objects"]]
            self.session.apply(scene_compiler.rebuild_parts(candidate, [part.name], existing), known=existing)
            trial = self._measure()
            if validation.worst(trial).silhouette_iou + 1e-4 < history[-1]:
                # Worse: put the previous geometry back and stop — never keep a regression.
                existing = [o["name"] for o in self.session.scene()["objects"]]
                self.session.apply(scene_compiler.rebuild_parts(self.model, [part.name], existing), known=existing)
                reason = "improvement plateaued"
                break
            self.model = candidate
            current = trial
            history.append(validation.worst(current).silhouette_iou)
            for r in current:
                r.iteration = it
        for r in current:
            r.plateau_reason = reason
        return first, current, reason

    def _calibrate_camera(self) -> None:
        """Reproduce the reference camera: the elevation whose render best matches the picture."""
        from . import calibration
        snap = self.session.snapshot()
        ref = self.facts["mask"]
        mm = self.facts["scale_mm_per_px"]
        cam = self.model.cameras[0]

        def score(e):
            cam.elevation_deg = e
            ren, _, _ = validation.model_silhouette(snap["tris"], "perspective", mm_per_px=mm, camera=cam)
            return validation.compare(ref, ren, view="perspective", align="shape").result.silhouette_iou

        best, iou, _ = calibration.fit_elevation(score)
        cam.elevation_deg = best
        cam.confidence = round(min(0.8, iou), 3)
        self.facts["camera_fit"] = {"elevation_deg": best, "iou": iou}

    def _silhouettes(self) -> dict:
        try:
            snap = self.session.snapshot()
            size = max(max(p.dimensions()) for p in self.model.parts if p.role == "part") or 100
            return {v: validation.model_silhouette(snap["tris"], v, mm_per_px=size / 240)[0]
                    for v in ("front", "side", "top")}
        except (BridgeError, ValueError):
            return {}

    # ================================================================ presenting
    def _present(self) -> bool:
        """Open the finished project in a Blender window of our own, framed on the model."""
        if not self.cfg.present or not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return False
        if self.session and not self.session.background:
            try:
                self.session.call("present")
            except BridgeError:
                pass
            return True
        try:
            gui = BlenderSession([str(self.root)], background=False, open_file=str(self.project.blend),
                                 memory_mb=self.cfg.memory_mb, owner=self.job.id if self.job else "jarvis",
                                 factory_startup=False).start()
        except BridgeError:
            return False
        old, self.session = self.session, gui
        gui.last_checkpoint = str(self.project.blend)
        try:
            gui.call("present")
        except BridgeError:
            pass
        if old:
            old.close()
        self.focused = _raise_window(gui.proc.pid)
        return True

    def _honest_line(self) -> str:
        if self.model is None:
            return ""
        ev = self.model.evidence_summary()
        parts = []
        if self.model.fidelity == Fidelity.DIMENSIONAL:
            parts.append("It's built to the drawing's dimensions")
        elif self.model.fidelity == Fidelity.ARTISTIC:
            parts.append("It's an artistic interpretation")
        else:
            parts.append("It's a visual match, not an exact measurement")
        if ev.get("invented"):
            parts.append(f"I invented {_list(ev['invented'])} where there was no evidence" if self.job.mode != Mode.VECTOR
                         else "the thickness is my choice")
        elif ev.get("estimated"):
            parts.append(f"{_list(ev['estimated'])} {'is' if len(ev['estimated']) == 1 else 'are'} estimated")
        if self.job and self.job.mode in (Mode.PARAMETRIC, Mode.ORGANIC) and self.facts.get("kind") == "revolve":
            parts.append("the hidden side is assumed round")
        return "; ".join(parts) + "."

    def _ready_message(self, presented: bool, after: list, reason: str) -> str:
        where = "ready in Blender" if presented else "saved"
        worst = validation.worst(after) if after else None
        match = ""
        if worst is not None:
            match = f" The worst view matches {worst.silhouette_iou * 100:.1f}% by silhouette"
            if worst.dimension_error_mm:
                match += f", within {worst.dimension_error_mm:.2f} mm of the labelled size"
            match += "."
        return f"The first version is {where}. {self._honest_line()}{match}"

    # ================================================================ edits
    def _answer_pending(self, text: str):
        clar = self.pending
        t = text.lower()
        if re.search(r"\b(?:cancel|never ?mind|forget it)\b", t):
            self.pending = None
            return editing.EditOperation(target=[], operation="noop")
        chosen = [c for c in clar.candidates if re.search(rf"\b{re.escape(c.lower())}\b", t)
                  or re.search(rf"\b{re.escape(c.split()[0].lower())}\b", t)]
        if "unlock" in t and clar.pending.operation == "add_holes":
            host = editing._default_host(self.model)
            host.locked = []
            chosen = [host.name]
        if len(chosen) != 1:
            return None
        op = clar.pending
        op.target = chosen
        self.pending = None
        return op

    def _highlight(self, names: list) -> None:
        try:
            if self.session and self.session.alive():
                self.session.call("highlight", {"names": [n for n in names if self.model.part(n)]})
        except BridgeError:
            pass

    def _edit(self, op, text: str) -> str:
        self._recovered = ""
        reply = self._edit_inner(op, text)
        return (self._recovered + reply) if self._recovered else reply

    def _edit_inner(self, op, text: str) -> str:
        if op.operation == "noop":
            return "Okay, I left it as it is."
        try:
            session = self._session()
            if op.operation in ("undo", "redo", "restore"):
                return self._history(op)
            if op.operation == "undo_matching":
                return self._undo_matching(op.extra["what"])
            if op.operation == "export":
                return self._export(op.extra["profile"])
            if op.operation == "compare":
                return self._compare()
            if op.operation == "refine":
                before, after, reason = self._refine(self.job.mode if self.job else Mode.PARAMETRIC)
                return (f"Refined: worst view {validation.worst(before).silhouette_iou * 100:.1f}% → "
                        f"{validation.worst(after).silhouette_iou * 100:.1f}% ({reason})." if before else
                        "There's no reference view to match against.")
            if op.operation == "rebuild_back":
                return ("I'd need a view of the back to rebuild it from evidence. I can mirror the front instead — "
                        "that back would be invented. Say \"mirror the front\" if that's what you want.")
            try:
                out = editing.apply(self.model, op)
            except editing.Refused as exc:
                return str(exc)
            return self._apply_outcome(session, op, out, text)
        except BridgeError as exc:
            return self._recover(exc)

    def _apply_outcome(self, session, op, out: editing.Outcome, text: str) -> str:
        existing = [o["name"] for o in session.scene()["objects"]]
        before = {o["name"]: o["geometry_hash"] for o in session.snapshot()["objects"]}
        self.project.autosave(session)
        if op.operation in ("lock", "unlock"):
            ops = [{"op": "set_properties", "target": p.name, "props": {"jarvis_locked": ",".join(p.locked)}}
                   for p in out.model.parts if p.name in op.target and p.name in existing]
        elif not out.geometry:
            ops = []
            for p in out.model.parts:
                if p.name in out.changed and p.material:
                    mat = next(x for x in out.model.materials if x.name == p.material)
                    ops.append({"op": "assign_material", "target": p.name, "material": mat.name, "color": mat.color,
                                "metallic": mat.metallic, "roughness": mat.roughness})
        else:
            ops = scene_compiler.rebuild_parts(out.model, out.changed + out.added, existing)
            ops += scene_compiler.removed_parts_ops(out.removed, existing)
        if ops:
            try:
                session.apply(ops, known=existing)
            except BridgeError as exc:
                # A refused operation may leave earlier ones applied: go back to the checkpoint so
                # "nothing was modified" is true.
                if session.alive():
                    self.project.restore(session, self.project.current)
                raise exc
        problem = self._verify(session, out, before)
        if problem:
            self.project.restore(session, self.project.current)
            privacy.audit("edit.rolled_back", job=self.job.id if self.job else None, op=op.operation)
            return f"That edit didn't come out right ({problem}), so I rolled it back to version {self.project.current}."
        self.model = out.model
        n = self.project.checkpoint(session, self.model, _scrub(text), op.operation, out.changed + out.added,
                                    self._silhouettes() if out.geometry else None)
        privacy.audit("edit.applied", job=self.job.id if self.job else None, op=op.operation, version=n)
        return f"{out.message} That's version {n}."

    def _verify(self, session, out: editing.Outcome, before: dict) -> str:
        """Blender must now match the model; parts the edit did not name must be untouched."""
        scene = {o["name"]: o for o in session.scene()["objects"]}
        for name, dims in out.expect.items():
            if name == "materials":
                mats = {m["name"]: m for m in session.scene()["materials"]}
                for mname, want in dims.items():
                    got = mats.get(mname)
                    if got is None or abs(got["roughness"] - want["roughness"]) > 1e-3 or \
                            max(abs(a - b) for a, b in zip(got["color"], want["color"])) > 2e-3:
                        return f"the {mname} material did not update"
                continue
            obj = scene.get(name)
            part = out.model.part(name)
            if obj is None:
                return f"{name} is missing in Blender"
            if part is None or part.geometry not in ("box", "cylinder", "cutter"):
                continue
            got = [v * 1000 for v in obj.get("data_size", obj["size"])]
            if max(abs(a - b) for a, b in zip(got, dims)) > 0.05:
                return f"{name} measures {'×'.join(f'{g:.2f}' for g in got)} mm instead of {'×'.join(f'{d:.2f}' for d in dims)}"
        after = {o["name"]: o["geometry_hash"] for o in session.snapshot()["objects"]}
        touched = set(out.changed) | set(out.added) | set(out.removed)
        stray = [n for n, h in before.items() if n not in touched and n in after and abs(after[n] - h) > 1e-4]
        if stray:
            return f"{_list(stray)} changed as well"
        return ""

    def _history(self, op) -> str:
        session = self.session
        if op.operation == "undo":
            model = self.project.undo(session)
            if model is None:
                return "There's nothing earlier to go back to."
        elif op.operation == "redo":
            model = self.project.redo(session)
            if model is None:
                return "There's nothing to redo."
        else:
            try:
                model = self.project.restore(session, int(op.amount))
            except KeyError as exc:
                return str(exc).strip("'").capitalize() + "."
        self.model = model
        if not session.background:
            try:
                session.call("present")              # reopening a file resets the view; frame the model again
            except BridgeError:
                pass
        v = self.project.version(self.project.current)
        names = {o["name"] for o in session.scene()["objects"] if o["role"] in ("part", "cutter")}
        want = {p.name for p in model.parts}
        if names != want:
            return f"I reopened version {v.number}, but Blender's parts don't match its record — please check it."
        return f"Back to version {v.number} ({v.note})."

    def _undo_matching(self, what: str) -> str:
        words = set(re.findall(r"[a-z]+", what.lower()))
        for v in reversed(self.project.versions()[: self.project.current]):
            if v.number <= 1:
                break
            hay = set(re.findall(r"[a-z]+", (v.note + " " + v.op + " " + " ".join(v.parts)).lower()))
            stems = {w.rstrip("s") for w in hay}
            if words & hay or {w.rstrip("s") for w in words} & stems:
                if v.number == self.project.current:
                    self.project.restore(self.session, v.number - 1)
                    self.model = self.project.load_model()
                    return f"Undid that — back to version {self.project.current}."
                # Later edits exist: take out just those parts, as a new version.
                added = [p for p in v.parts if self.model.part(p) and self.model.part(p).role == "cutter"]
                if not added:
                    return "That change is under later edits; say \"go back to version " \
                           f"{v.number - 1}\" to return to before it."
                m = copy.deepcopy(self.model)
                m.parts = [p for p in m.parts if p.name not in added]
                out = editing.Outcome(m, changed=sorted({self.model.part(a).params.get("host") for a in added}),
                                      removed=added, message=f"Removed {_list(added)}.")
                return self._apply_outcome(self.session, editing.EditOperation(target=[], operation="remove"), out,
                                           f"undo {what}")
        return f"I can't find a change to the {what} to undo."

    def _export(self, profile: str) -> str:
        prof = exports.PROFILES[profile]
        fmt = prof["format"]
        path = self.project.exports_dir() / f"{re.sub(r'[^A-Za-z0-9]+', '-', self.project.name)}{prof['suffix']}.{fmt}"
        kwargs = {"max_faces": prof["max_faces"]} if prof["max_faces"] else {}
        r = self.session.export(fmt, str(path), **kwargs)
        if fmt == "stl":
            lo, hi = scene_compiler.bounds_mm(self.model)
            voxel = float(r.get("remeshed_voxel_mm") or 0.0)
            chk = exports.validate_stl(str(path), r.get("checks"), [hi[i] - lo[i] for i in range(3)],
                                       tolerance_mm=0.5 + 2 * voxel)
            if r.get("remeshed_voxel_mm"):
                chk.warnings.append(f"the parts didn't join cleanly, so the print copy was rebuilt as one watertight "
                                    f"solid at {r['remeshed_voxel_mm']:.2f} mm detail (the Blender model is unchanged)")
        elif fmt == "glb":
            chk = exports.validate_glb(str(path), prof["max_faces"])
        else:
            chk = exports.VALIDATORS[fmt](str(path))
        if self.job:
            self.job.exports.append({"format": fmt, "profile": profile, "ok": chk.ok, "faces": chk.faces,
                                     "problems": chk.problems, "warnings": chk.warnings})
            self.book.save(self.job)
        privacy.audit("export", job=self.job.id if self.job else None, format=fmt, ok=chk.ok, count=chk.faces)
        where = f" Saved to {path.parent.name}/{path.name}."
        if profile == "print":
            if chk.ok:
                bed = chk.facts.get("bed", {}).get("suggestion", "")
                extra = f" Print it {bed}." if bed else ""
                return ("The STL passes the watertight, normals and self-intersection checks at real scale (mm)."
                        + (" " + "; ".join(chk.warnings) + "." if chk.warnings else "") + extra + where)
            return "The STL was written but it isn't ready to print: " + "; ".join(chk.problems) + "." + where
        if profile == "game":
            return (f"Game-ready copy exported: {chk.faces} triangles with UVs, under the {prof['max_faces']} budget."
                    if chk.ok else "The game copy has problems: " + "; ".join(chk.problems) + ".") + where
        return chk.summary() + where

    def _compare(self) -> str:
        cur = self.project.current
        prev = cur - 1
        if prev < 1:
            return "There's only one version so far."
        from PIL import Image
        rows = []
        ious = []
        for view in ("front", "top"):
            a, b = self.project.silhouette(prev, view), self.project.silhouette(cur, view)
            if a is None or b is None:
                continue
            H, W = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
            A = validation._paste((H, W), a, (H - a.shape[0], 0))
            B = validation._paste((H, W), b, (H - b.shape[0], 0))
            ious.append(validation.imaging.iou(A, B))
            diff = np.zeros((H, W, 3), np.uint8) + 255
            diff[A & B] = (150, 150, 150)
            diff[A & ~B] = (220, 60, 60)
            diff[~A & B] = (40, 170, 80)
            gray = lambda M: np.where(M[..., None], 60, 245).repeat(3, axis=2).astype(np.uint8)
            rows.append(np.concatenate([gray(A), np.full((H, 8, 3), 255, np.uint8), gray(B),
                                        np.full((H, 8, 3), 255, np.uint8), diff], axis=1))
        if not rows:
            return "I don't have silhouettes for those versions."
        W = max(r.shape[1] for r in rows)
        img = np.concatenate([np.pad(r, ((4, 4), (0, W - r.shape[1]), (0, 0)), constant_values=255) for r in rows])
        out = self.project.path / "renders" / f"before-after-v{prev:03d}-v{cur:03d}.png"
        Image.fromarray(img).save(out)
        try:
            subprocess.Popen(["xdg-open", str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
        return (f"Showing version {prev} beside version {cur}: grey is unchanged, red was removed, green was added. "
                f"The silhouettes overlap {min(ious) * 100:.1f}%.")

    def _recover(self, exc: BridgeError) -> str:
        privacy.audit("bridge.failure", job=self.job.id if self.job else None, error=exc.category.value)
        if exc.category in (ErrorCategory.BLENDER_CRASHED, ErrorCategory.BRIDGE_LOST, ErrorCategory.RESOURCE_LIMIT):
            try:
                if self.session is not None:
                    self.session.last_checkpoint = str(self.project.blend) if self.project else None
                    self.session.restart()
                    self.model = self.project.load_model() if self.project else self.model
                return (f"Blender stopped ({_bridge_words(exc)}). I restarted it from version "
                        f"{self.project.current}; nothing saved was lost, but that last change wasn't applied.")
            except BridgeError as again:
                return f"Blender stopped and couldn't be restarted ({_bridge_words(again)}). The saved project is intact."
        return f"Blender refused that change ({_bridge_words(exc)}). Nothing was modified."


# ============================================================================ helpers
def _list(names) -> str:
    names = [str(n).lower() for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _scrub(text: str) -> str:
    from ..selfrepair.jobs import scrub
    return scrub(text, 100)


_GENERIC = {"this", "that", "it", "object", "thing", "model", "image", "picture", "screen", "reference", "one"}


def _name_from(request: str) -> str:
    """'make a 3D model of this bracket' → 'Bracket'. Generic words give 'Model'."""
    m = re.search(r"(?i)\bof\s+(?:this|the|my|a|an|that)\s+([a-z][a-z -]{1,30}?)(?=\s+(?:on|from|in|into|with|for|as)\b|[.,!?]|$)",
                  request or "")
    words = [w for w in (m.group(1).split() if m else []) if w.lower() not in _GENERIC]
    name = " ".join(words[-2:]).title()
    return re.sub(r"[^A-Za-z0-9 ]", "", name)[:30] or "Model"


def _bridge_words(exc: BridgeError) -> str:
    return {ErrorCategory.BLENDER_UNAVAILABLE: "Blender isn't available",
            ErrorCategory.BLENDER_CRASHED: "Blender crashed",
            ErrorCategory.BRIDGE_LOST: "the connection to Blender dropped",
            ErrorCategory.RESOURCE_LIMIT: "it hit a memory or time limit",
            ErrorCategory.INVALID_PLAN: "an operation was refused as invalid",
            ErrorCategory.CANCELLED: "cancelled"}.get(exc.category, str(exc)[:120])


def _raise_window(pid: int, timeout: float = 20.0) -> bool:
    """Bring our Blender window (by its process id) to the front, once, when the result is ready."""
    import shutil
    if not shutil.which("xdotool"):
        return False
    end = time.time() + timeout
    while time.time() < end:
        try:
            ids = subprocess.run(["xdotool", "search", "--pid", str(pid), "--onlyvisible", "--name", "Blender"],
                                 capture_output=True, text=True, timeout=5).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            return False
        if ids:
            try:
                subprocess.run(["xdotool", "windowactivate", ids[-1]], capture_output=True, timeout=5)
                return True
            except (OSError, subprocess.TimeoutExpired):
                return False
        time.sleep(0.5)
    return False


def focused_window() -> tuple:
    """(app, title) of the focused window, for the capture privacy check. ("", "") if unknown."""
    try:
        from ..screen_context import active_window
        return active_window()
    except Exception:  # noqa: BLE001
        return "", ""


_STUDIO: Optional[Studio] = None
_STUDIO_LOCK = threading.Lock()


def get() -> Studio:
    global _STUDIO
    with _STUDIO_LOCK:
        if _STUDIO is None:
            _STUDIO = Studio()
            _STUDIO.book.recover_interrupted()
        return _STUDIO


def use(studio: Optional[Studio]) -> None:
    """Tests: install (or clear) the studio the command handler talks to."""
    global _STUDIO
    _STUDIO = studio
