# The teaching overlay

Jarvis draws and animates explanations over the desktop while he speaks. This is how it is
built, what each piece guarantees, and what was measured.

## Pieces

```
voice process                         backend (:8770)            overlay (Electron, XWayland)
─────────────                         ───────────────            ────────────────────────────
VoiceSession._turns                                              main.js → teach/main-teach.js
  └ _teach_turn ─► teach.assistant     /emit {kind:"teach"} ──SSE──►  validate (protocol.js)
       intents → lesson / control         │                           place on monitor, show
       runner.LessonRunner                │                           └► teach.html (sandboxed)
         Speaker.speak(phrases,           │                                renderer.js: SVG + canvas
           on_start(i, audible_at)) ──────┘                                validate again
         SceneModel (mirror, undo,      /emit {kind:"teach_event"} ◄── frame / idle / anim / dismissed
           snapshots)                  ◄──SSE── bus.Overlay listener
```

| File | Role |
|---|---|
| `overlay/teach/protocol.json` | The whole command language: ops, object types, fields, limits. Read by both validators. |
| `jarvis/teach/protocol.py`, `overlay/teach/protocol.js` | Strict validators; `tests/test_teach_protocol.py` runs every case through both. |
| `overlay/teach/main-teach.js` | The window: transparent, `focusable: false`, click-through, mapped only while something is on it; SSE subscription; pen mode; SIGURG and control-socket dismissal; crash and display-change recovery. |
| `overlay/teach/renderer.js`, `geometry.js`, `teach.css` | SVG for diagram objects (crisp at any scale), a DPR-sized canvas for manual ink; a timeline that runs only while something moves. |
| `jarvis/teach/bus.py` | Envelopes (lesson, generation, sequence, `at`, `sent`), a non-blocking ordered sender, the event listener, metrics, display/work-area lookup. |
| `jarvis/teach/scene.py` | Command builders and the scene mirror (undo/redo, step snapshots, "that"). |
| `jarvis/teach/plan.py` | `LessonPlan` / `Step` / `Phrase`: speech and the commands that belong to each phrase. |
| `jarvis/teach/runner.py` | Plays a plan through a `Speaker`; pause, resume, back, skip, again, follow-ups, cleanup, generations. |
| `jarvis/teach/lessons/pythagoras.py`, `rag.py` | Deterministic templates in `en`, `hinglish`, `hi` (Devanagari Hinglish), `hi-pure`. |
| `jarvis/teach/lessons/generic.py` | Any subject: the model returns lesson JSON (nodes, edges, formula, steps); `check` keeps only what is valid and safe, `place` lays it out (flow, cycle, tree, layers, timeline, compare) and routes edges around boxes, `build` makes the plan; follow-ups by node or by model with the lesson as context. |
| `jarvis/teach/screen_lesson.py`, `triangle.py` | The video lesson: find, pause + verify, ground, locate, detect, trace, track. |
| `jarvis/teach/intents.py`, `assistant.py` | What a request means; the one entry point for voice and typed requests. |

## Protocol

A batch is `{v, lesson, gen, seq, at?, sent?, cmds[]}`. Operations: `scene.create/update/clear`,
`shape.add/update/remove`, `group.add/remove`, `stroke.draw`, `highlight.show/hide`,
`focus.show/hide`, `timeline.play/pause/resume/seek/cancel`, `anchor.attach/detach`. Objects:
stroke, highlighter, line, arrow, rect (rounded with `r`), circle, ellipse, triangle, right_angle,
text, equation (terms with superscripts, individually highlightable as `id#term`), node, edge,
region, spotlight, pulse, panel.

Refused: unknown ops, types or fields; any text that looks like markup, a URL, a script scheme or
a local path; non-finite or out-of-range numbers; zero or negative sizes; degenerate triangles;
animations over 12 s; schedules more than 30 s ahead; anchors older than 6 s; monitors that don't
exist; more than 160 commands, 400 objects, 4000 points or 400 KB. The model never writes
commands: lessons are code, and a language model at most chooses words.

Coordinates are logical pixels relative to the monitor's top-left. The window covers the
monitor's work area (not the whole monitor — Mutter promotes a borderless window the size of the
screen to fullscreen and hides the top bar), and the SVG viewBox starts at the work area's
offset, so a coordinate is never converted twice.

## Speech and pictures

`local_tts.speak_segments` plays each phrase as its own unit and calls `on_start(i, at_ms)` when
phrase *i*'s first audio is written to the device, with `at_ms` = write time + the stream's
output latency. The runner sends that phrase's batch stamped `at`; the renderer draws it at
`at` and reports the frame time. Drift is `frame − at`, measured per phrase. Batches are posted
from a sender thread: a POST on the audio thread once stalled the voice by 390 ms.

Generations are wall-clock milliseconds, never decreasing: a new lesson, a replay or a clear
bumps it, the renderer drops anything older and cancels what an older generation had scheduled,
and the runner ignores speech callbacks from a superseded run (run tokens).

Barge-in stops the voice (the voice session's own monitor); the runner then sends
`timeline.pause`, which freezes every animation in place. "Continue" cancels that step's
half-drawn objects, redraws its earlier phrases instantly, and says the cut phrase again. "Go
back" restores the snapshot taken before the previous step, instantly, and teaches it again.

## Anchors

Priority: DOM, AT-SPI, detected region, verified coordinates, standalone. On GNOME Wayland,
AT-SPI boxes start at 0,0 and are not screen positions, so native anchors are not used for
drawing. A browser element maps to the screen only when the window's position is known:
fullscreen, or maximised (outer size equals the work area) — then `workArea + mozInnerScreen +
rect × devicePixelRatio / scale`. Before tracing, the screenshot crop must show the YouTube
player's red progress bar: a tab reports itself visible while its window is on another workspace.

A traced lesson's objects are attached to the `video` anchor. `AnchorTracker` re-reads the page
every 0.4 s: moved → the drawing moves; playing again, navigated, hidden or unreachable →
confidence 0 and the renderer hides it. The renderer also hides anchored objects whose anchor has
not been refreshed within its TTL.

## Safety

Click-through by default (X11 input region; see limits below), no focus ever, mapped only while
something is on it. Pen mode ends by Done, `Ctrl+Super+P`, two idle minutes (renderer) or five
minutes (main), and always restores click-through. Emergency dismissal hides first and tells
others after. The renderer page has a CSP with no inline or remote script, a sandbox, and only
named IPC channels. Rejections are reported by field name, never content; the runner's log holds
numbers and step indexes only; the video lesson's screenshot is cropped in memory and deleted.
Scenes are cleared when the backend stream drops, the voice process restarts, a display changes,
or the renderer crashes (the voice process is told and redraws the current step).

## Measured on this machine (GNOME 49 Wayland, 1920×1080 @ 144 Hz, scale 1.0)

| | |
|---|---|
| Request → first overlay frame (typed/voice path, deterministic) | 45 ms |
| Command received → frame (renderer) | p50 3–6 ms, p95 7–32 ms |
| Speech → picture drift, real Kokoro/Piper audio | p50 1 ms, p95 5 ms, max 5 ms (13 phrases, no screenshots running) |
| Drift outliers observed | 114 ms on a first phrase (mapping the hidden window); 283–386 ms coinciding with a portal screenshot taken by the test itself |
| Animation | 6.8–11 ms mean frame (≈ 90–144 fps), worst single frames 20–125 ms |
| Emergency dismissal | window unmapped 0.6 ms after SIGURG in the main process; 3–4 ms end to end via the socket; voice stopped 39 ms after |
| Barge-in style stop | voice stopped 33–36 ms after the stop, pictures frozen |
| Hidden overlay CPU | ≤ 0.2 % per process, ≈ 0.5 % total for the whole overlay (HUD included) |
| Hidden overlay input | unmapped; while shown, input region 1×1 px (Chromium's), full work area only in pen mode |
| Video lesson: pause verified / transcript / frame captured | 0.33 s / 2.8 s / 3.8 s |

## Known limits

* **One pixel.** Chromium implements "ignore mouse events" on X11 as a 1×1 input rectangle at the
  window's top-left — screen pixel (0, 28) here. Everything else passes through.
* **Tracing the teacher's triangle was not seen live.** During testing Zen was on another
  workspace; the pixel check correctly refused to trace and the lesson said so. The tracing
  path is tested with synthetic frames only.
* **"This" is overlay-only.** It resolves to the most recently drawn or referenced overlay object.
  The pointer position (XWayland only sees it over X11 windows), text selections and AT-SPI focus
  are not used; with nothing to point at, Jarvis asks.
* **Typed controls don't reach a spoken lesson.** The backend and the voice process each have
  their own runner; "go back" typed into the HUD affects a lesson started from the HUD.
* **Any-topic lessons need the strong model.** Groq's per-minute token cap allows roughly two
  lesson-sized requests a minute; Gemini currently answers `permission_denied` for the configured
  key. When neither answers, Jarvis says so and draws nothing. Pythagoras and RAG are hand-built
  and work offline.
* **A lesson owner that exits without clearing leaves its drawing** until the next lesson, a
  voice-process restart (which resets the overlay) or the emergency shortcut.
