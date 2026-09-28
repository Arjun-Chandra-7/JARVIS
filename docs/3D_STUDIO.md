# JARVIS 3D Studio

"Jarvis, make a 3D model of this." JARVIS captures the reference from the screen (or takes an
image file, the clipboard, or video frames), reconstructs it as a **named, parametric, editable**
Blender model, checks the model against the reference with real measurements, opens it in its
own Blender window, and keeps editing it by voice — with versions, undo and verified exports.

Code: `jarvis/three_d/`. Tests: `tests/test_three_d_*.py`. Demo: `scripts/demo_3d_studio.py`.

**Blender is the editable source of truth.** Tinkercad is not used (browser automation is too
fragile and its modelling too limited); an export adapter could be added later.

---

## What it can and cannot know

A single screenshot does not contain hidden surfaces, true depth, absolute scale, internals,
exact lens distortion, or material properties outside the view. JARVIS says so every time.

Every part records its evidence, per aspect (outline, scale, depth, colour …):

| Class | Meaning |
|---|---|
| `verified` | directly supported by a measurement (a labelled dimension) or several views |
| `constrained` | inside supplied dimensions / stated symmetry (e.g. scaled from a labelled overall size) |
| `estimated` | inferred from visual evidence alone |
| `invented` | made up because there was no evidence (a logo's thickness, an unseen back) |

The whole result is labelled one of: **dimensionally accurate reconstruction**, **visually
matched reconstruction**, **artistic interpretation**. JARVIS never says "exact" unless labelled
measurements back it; asked for exact depth from one picture it asks one question instead.

| Input | What to expect |
|---|---|
| Logo / icon / flat line art | near pixel-perfect contour (measured: IoU 0.997–0.998, mean contour error 0.06 px); thickness invented unless stated |
| Dimensioned orthographic drawings | parametric model to the labels (measured: ≤ 0.03 mm from labelled sizes, every view IoU ≥ 0.999) |
| Front/side/top without labels | proportions correct; scale estimated or from one stated measurement |
| Turntable frames / orbit video file | exterior visual hull; concavities not recoverable (marked invented) |
| One perspective screenshot | visually matched approximation (measured on a hidden-truth render: IoU 0.985, profile error 2.4 % of height); depth and scale estimated |
| Hidden / internal geometry | needs more references or explicit instructions |

## Architecture and trust boundaries

```
voice/typed turn ──► jarvis.commands.handle ──► three_d.commands (routing only)
                                                    │
                                     Studio (one worker thread, jarvis/three_d/studio.py)
   capture ─► privacy ─► classifier/planner ─► engines ─► scene_compiler ─► ScenePlan (JSON)
                                                                             │  validated here
   ───────────────────────── Unix socket, 0600, token + peer PID ────────────┼──────────────
                                                                             ▼  validated again
                                  Blender (started by JARVIS) ─ blender_server.py: fixed ops only
```

* **Untrusted:** everything in a reference image, including any text in it; model output; files
  outside the project folder; other local processes.
* **ScenePlan** (`scene_ops.py`) is the only thing Blender receives: a list of allowlisted
  operations — create primitive / curve / revolve / mesh, extrude, bevel, boolean, mirror,
  array, subdivision, solidify, remesh (only after a checkpoint), set transform / dimensions,
  material, parent, properties, visibility, camera, light, reference plane, delete. Unknown
  operations, unknown arguments, non-finite or negative sizes, arrays over 64, subdivision over
  3, textures over 2048 px, over 400 000 faces, unsafe names and paths are refused.
* **Text in images** is read locally only to find numbers (dimension labels). Instruction-shaped
  text is logged as `security.image_text_ignored` with no content and otherwise ignored.

### Package map

| Module | Job |
|---|---|
| `types.py` | `ReferenceAsset`, `EvidenceView`, `ReconstructionJob`, `ModelPart`, `StudioModel`, `EditOperation`, `ValidationResult`, evidence/fidelity/mode enums |
| `scene_ops.py` | the operation allowlist, limits and path confinement (stdlib only; imported by Blender too) |
| `capture.py`, `privacy.py` | screen region / window / salient-object / file / clipboard / video capture; sensitive-screen refusal, shredding, retention, content-free audit |
| `classifier.py`, `imaging.py` | measured features → mode |
| `calibration.py` | units, dimension labels, scale, camera fitting by analysis-by-synthesis |
| `planner.py` | runs the engine, validates the model and plan, asks the one necessary question |
| `vector_reconstruction.py` | colour layers → contours with holes → extruded, bevelled curves |
| `parametric_reconstruction.py` | orthographic drawings → boxes/cylinders with mirror/array; one view → revolve/extrude |
| `multiview.py` | turntable silhouettes → consistency check → carved visual hull |
| `generative.py` | optional image-to-3D providers (none installed), GLB reading |
| `scene_compiler.py` | model → ScenePlan; incremental rebuilds of just the affected parts |
| `blender_bridge.py`, `blender_server.py` | the restricted bridge (client / in-Blender server) |
| `validation.py` | CPU rasteriser of Blender's evaluated triangles, metrics, loop control |
| `editing.py` | voice edits → typed operations, locks, targets, clarifications |
| `project.py` | project folders, atomic versions, undo/redo/restore, autosave |
| `exports.py` | export profiles and independent file validators |
| `jobs.py`, `studio.py`, `commands.py` | background job lifecycle, orchestration, voice routing |
| `resources.py` | GPU/RAM/disk gates protecting the live voice service |

Reused from JARVIS: the Wayland portal screenshot (`vision/screenshot.py`), local OCR
(`vision/ocr.py`), the approval manager (`approvals.py`, new kind `upload`), desktop
notifications and spoken announcements (`jobs/notify.py`), the overlay event channel
(`/emit`, new kind `studio`), the scrubber from `selfrepair/jobs.py`.

## Blender bridge

* Blender 5.2.2 LTS, official blender.org tarball (sha256 verified) in
  `~/.local/opt/blender-5.2.2`, linked as `~/.local/bin/blender`. Override with `JARVIS_BLENDER`.
  Supported range 4.2 – 5.x, checked at launch and again at connect.
* JARVIS always starts **its own** Blender (`--disable-autoexec`; headless with
  `--factory-startup`), so a project the user has open elsewhere is never touched.
* Socket: `$XDG_RUNTIME_DIR/jarvis-3d/bridge-*.sock`, directory 0700, socket 0600. No TCP.
* Auth: 256-bit token in a 0600 file that Blender reads and deletes at start; every connection
  must say `hello` with it; `SO_PEERCRED` must be this user **and** the JARVIS process id —
  another local process holding the token is still refused (tested).
* Commands: `info, new_project, clear_project, apply, scene, snapshot, render, save, open,
  export, mesh_check, highlight, present, shutdown` (+ `ping`, `cancel` on any connection).
  There is no Python, script, shell or operator passthrough (a test greps the server for
  `exec/eval/subprocess/os.system/importlib`).
* Only objects Blender created for JARVIS (`jarvis_owned`) can be changed or deleted.
* Paths resolve symlinks and must stay inside the projects root (traversal and symlink escape
  tested on both sides).
* Saves are atomic (copy beside, rename over). A failed save leaves the old file intact (tested).
* Heartbeat (`ping` answers from the socket thread, with main-thread idle time), an RSS
  watchdog (default 4 GB), per-call time limits (a render over budget kills Blender), cancel
  between operations. A crash is detected and the next call restarts Blender on the last
  checkpoint and says so. If the JARVIS process dies, a headless Blender exits and a window
  stops listening.
* The presentation window runs under XWayland so JARVIS can bring it to the front once the
  result is ready (native Wayland windows cannot be raised by another program).
  `JARVIS_3D_NATIVE_WAYLAND=1` disables that.

## Reconstruction modes

| Mode | Chosen when | Pipeline |
|---|---|---|
| vector | ≤ 6 flat colours, crisp edges | background out → k-means layers → contours with holes (traced at 4× for ⅛-px accuracy) → curve per layer → extrude + bevel (curve settings, still editable) |
| dimensioned | line drawings with labels, named views | object strokes vs. annotation → closed regions → rect/circle fit with measured stroke width → units (asked if ambiguous) → scale per view → cross-view matching → boxes/cylinders → identical symmetric pairs become one part with a Mirror about the base, ≥3 evenly spaced become an Array → parent to base |
| parametric | shaded, mirror-symmetric | silhouette → solid of revolution (hidden side *assumed round*), camera elevation fitted by rendering → profile refined against the silhouette |
| organic | shaded, asymmetric | rounded extrusion (depth invented); a generative base mesh only through an approved provider |
| multiview | ≥ 4 frames / video file | per-frame silhouettes → reject frames whose size disagrees (ORB landmark matches as a second signal) → voxel carving → surface mesh → source cameras kept |
| artistic | "invent the back", "full character" | as above, every part labelled invented, result called an artistic interpretation |

### The accuracy loop

For each reference view: reproduce the camera (orthographic for drawings; fitted elevation for a
photo) → rasterise Blender's evaluated triangles (on the CPU — no VRAM taken from the voice
service) → measure silhouette IoU, contour distance, landmark error, proportion error,
dimension error, colour ΔE → adjust the part that explains the largest mismatch → repeat until
tolerance, plateau (gain < 0.002 over 2 steps), budget (8), or missing evidence. A step that
makes things worse is reverted. The worst view is reported, so a good front cannot hide a bad
side. Dimensioned models are never nudged away from their labels to match a picture.
A worst view under 60 % means the capture was wrong, not the model: the capture is shredded and
JARVIS asks for the area.

## Voice commands

Starting (always recognised): "make a 3D model of this", "turn the object on my screen into a
Blender model", "…of this logo, 150 mm wide", "…from this window", "…from the clipboard",
"…of /path/to/image.png", "show me while you make it".

While a 3D project is active (used in the last 30 minutes; undo/redo/version in the last 10):

* References: "use this image as the front view", "this is the side view", "take another
  reference after I rotate it", "the full object is 42 centimetres wide", "they're in inches",
  "just estimate it", "ignore the background", "delete the references".
* Edits: "make the base 15 percent wider", "increase the post diameter to 18 inches", "move the
  right block slightly inward", "add two evenly spaced holes", "undo the holes", "round only
  the top corners", "make the metal darker and less reflective", "add another identical post on
  the left", "the engraving should be deeper", "make the handle more curved", "lock the drum
  shells; only edit the hardware", "unlock the base", "match the reference silhouette more
  closely".
* History: "undo the last change", "redo", "go back to version three", "show me before and
  after".
* Output: "export it for 3D printing", "make a game-ready copy", "export it as glb/obj/fbx".
* Jobs: "how's the model going?", "pause/resume/cancel the model", "use the remote 3D provider".

Ambiguous targets are highlighted in Blender and asked about ("Which one — Block 1 or Block 2?").
Editing a locked part is refused; holes whose natural host is locked prompt a choice.
Every edit: autosave → change only the affected parts → verify sizes in Blender and that no other
part's geometry changed → checkpoint, or roll back to the last version.

## Export profiles

| Profile | File | Checks (read back from the file, not trusted from Blender) |
|---|---|---|
| project | `.blend` (+ `versions/vNNN.blend`) | atomic save |
| web | `.glb` ≤ 50 k faces | glTF 2.0, UVs, normals, face budget, size |
| game | `-game.glb` ≤ 20 k faces | triangulated, box-projected UVs, decimated to budget |
| obj / fbx | `.obj`, `.fbx` | geometry present / binary FBX header |
| print | `-print.stl`, **millimetres** | parts unioned; watertight, manifold, consistent winding, positive volume, Blender BVH self-intersection check, size against the model, thinnest-wall warning (< 1 mm), bed-orientation suggestion |

If parts don't union cleanly (touching coplanar layers of a logo), the print copy is rebuilt as
one voxel solid (0.1–1 mm, stated in the reply) — the Blender model is unchanged. JARVIS never
calls a model printable because an STL was written.

## Providers and resources

Routing: deterministic vector → parametric Blender → local CV → configured vision model →
optional remote image-to-3D (approval) → ask for another reference.

* **TRELLIS.2** (Microsoft, MIT): ≥ 8 GB VRAM (16–24 GB for full quality). **Hunyuan3D-2.1**
  (Tencent Hunyuan Community Licence; excludes the EU, UK, South Korea): ~10 GB for shape,
  ~21 GB with textures. This laptop has an RTX 3050 with 4 GB, ~1.5 GB of it used by the live
  voice stack — neither runs here, and JARVIS does not download weights. A large model needs a
  remote GPU.
* **Remote endpoint:** set `JARVIS_3D_REMOTE_URL` (HTTPS only). It is offered only when the Daily
  Brain's privacy policy allows picture uploads (Brain on, not "always local", screenshot upload on),
  and sending needs an approval that names the host ("yes, send it to mesh.example.com") — said, not
  clicked; a bare "yes" does not count.
* **Routing:** every reconstruction mode and edit is a Daily Brain decision
  (`three_d/brain_routes.py`) that resolves to 3D Studio's local engines — no general model router
  lives in this package.
* Cloud vision (Gemini) is never sent a 3D reference; analysis here is local.
* Measured: headless Blender ~330 MB RSS; a Workbench preview ~580 MB VRAM (renders only when
  the GPU keeps ≥ 300 MB free after it); build 1.8–3.6 s for the demos; one render at a time.
  Blender won't start with < 1.2 GB RAM or < 800 MB disk free.

## Privacy and retention

* The portal gives the whole screen; it is cropped in memory and the full frame **shredded**
  before anything else. Notification banners are switched off for the moment of capture.
* The focused window's title and the crop's words (local OCR) are checked; password, OTP,
  banking/payment, password-manager and private-chat screens are refused and nothing is kept.
* The salient crop only merges pieces that nearly touch the object, so a banner elsewhere is
  not swept in.
* Crops live in `<project>/refs/` (0600), swept after 24 h, removed by "delete the references".
  Video frames are transient.
* Logs (`3d-studio-audit.jsonl`, `3d-jobs.json`) hold ids, states, categories, counts — never
  pixels, OCR text or reference paths. Nothing is uploaded without a named approval.

## Failure recovery

| Failure | Behaviour |
|---|---|
| Blender missing / wrong version | job fails safely with that reason; JARVIS keeps working |
| crash / disconnect | detected; restarted on the last checkpoint; reply says so |
| invalid operation | refused before running; if anything had applied, the checkpoint is reopened |
| out of memory / render too long | watchdog stops Blender (`resource_limit`); restart on checkpoint |
| failed save | previous file untouched (atomic) |
| JARVIS restarted mid-job | job marked failed-safely on start; saved versions intact |

## Known limitations

* The drawing parser handles rectangles and circles in clean orthographic line drawings; sloped
  faces, fillets, hidden-line conventions and section views are not parsed.
* Single-view reconstruction knows revolve and extrude; it does not segment a product into
  semantic parts (a drum kit becomes one silhouette). Local vision grounding ("capture only the
  drum kit") is not wired in; JARVIS crops the main object and says so.
* A video playing inside another app can't be read; a local video file can.
* Multi-view assumes a level turntable/orbit with evenly spaced angles.
* Curve bevels on very sharp logo tips can fold; the print copy is voxel-rebuilt when that
  happens.
* No generative provider is installed; organic shapes are rough.
* Camera capture is refused (not implemented beyond the permission gate).

## Manual test

```
.venv/bin/python scripts/demo_3d_studio.py
```

It shows a generated logo full-screen, and only if that window is in front: captures it, builds,
opens Blender in front, edits ("make the logo blue 20 percent taller"), verifies, undoes, exports
GLB and STL, validates both, deletes the references, and prints a JSON report. Projects go to
`~/Documents/JARVIS 3D/` (override with `JARVIS_3D_ROOT`).

By voice, after `jarvis restart` on a build that includes this branch: put a picture on a plain
background, say "Jarvis, make a 3D model of this, 10 centimetres wide", then "make it 20 percent
taller", "undo the last change", "export it for 3D printing".
