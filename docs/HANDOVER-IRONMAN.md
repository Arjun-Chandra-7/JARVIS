# Handover — modes, the Iron Man overlay, and what is still owed

Written at the end of a long session, for whoever picks this up next. Everything described as
working was verified on screen; everything described as unfinished was left unfinished on purpose
rather than assumed done.

## What landed (all on `main`, tests green at 1175)

**Study mode** — `jarvis/modes/study.py`, command in `jarvis/mode_command.py`.
"Hey Jarvis, study mode" closes games, Netflix, WhatsApp, Discord *and matching browser tabs*,
then keeps closing them every 20s (`voice_session._keep_study_mode`) until "exit study mode".
Apps are asked to quit (SIGTERM), never killed. Editor, terminals, browsers and Jarvis are on a
protected list. "What is study mode?" asks about it and does not arm it.

**Iron Man mode** — `jarvis/modes/ironman.py`.
Super key for the gather, closes everything except editor/browser/Spotify, tiles the survivors,
plays a synthesised power-up sting (`build_sting`, no sampled audio), announces itself.

**The overlay** — `overlay/ironman.{html,css,js}`, form wired through `overlay/main.js`.
A real full-screen transparent layer over the desktop, not a second app. Click-through except
where the pointer is over a `.frame` panel. Live data throughout: projects from `~/Madara/Dev`
(newest first, via `GET /projects`), CPU/GPU/RAM/disk gauges (`GET /stats`), Spotify with working
controls (`GET /spotify`, `POST /spotify/control`). Clicking a project calls
`POST /project/open`, which opens the folder in VS Code **and** cds a terminal to it.

## The two bugs I was mid-fix on when I stopped

### 1. "Jarvis, normal mode" cannot rescue a stuck overlay

`ironman.asked_to_stop()` matches "jarvis normal mode" correctly — that part is fine. The failure
is state, in two places:

* `jarvis/modes/ironman._on` is in-memory. After a Jarvis restart it is `None`.
* The overlay **persists its form** (`overlay/main.js` `saveState()` writes `state.form`), so it
  comes back up wearing the Iron Man frame.

Result: the frame is on screen, the backend believes the mode is off, and `mode_command` returns
`None` for "normal mode" (see the `if not ironman.on(): return None` guard) — so it falls through
to the model and nothing happens. The user is stuck in the frame.

Fix, both halves:
* Never restore `ironman` as a remembered form — start in `pill` always. `applyForm`/`loadState`
  in `overlay/main.js`.
* Make "normal mode" unconditional: even when `ironman.on()` is false, emit
  `{"kind":"mode","text":"pill"}` so a stuck overlay is rescued. Drop the `return None` guard, or
  keep it only when the overlay is demonstrably not in the ironman form.

### 2. The framing places only VS Code, and places it outside the frame

`ironman.lay_it_out()` tiles against the **whole screen**. The overlay draws rails and bars over
the edges, so windows tiled to the full screen sit underneath them. It must tile to the inner
rect, using the same fractions the CSS uses:

    rails: 12.6vw each side      bars: 4.6vh top and bottom
    inner = (0.126*W, 0.046*H) to (W - 0.126*W, H - 0.046*H)

Then: editor in the left half of `inner`, ChatGPT top-right, terminal bottom-right.

It also only places windows that already exist. On this machine there was no ChatGPT window and no
terminal window, so only the editor moved. It needs to launch what is missing first, then wait for
the windows to appear before placing:

    ChatGPT   google-chrome --app=https://chatgpt.com/     (/usr/bin/google-chrome is present)
    terminal  x-terminal-emulator                          (the GNOME default here)

Match windows by title: the editor contains "visual studio code", ChatGPT contains "chatgpt", the
terminal is whatever `x-terminal-emulator` opens — check its actual title with `wmctrl -l` rather
than guessing.

## Also unfinished

* **The HUD's top and bottom bars do not match the reference image.** The side rails are right.
  The clock and tray were wrapping; that specific cause is fixed (see below) but the bars have not
  been re-verified since and still want a layout pass against the screenshot the user supplied.
* **Multi-user: not started.** Installation wizard, licence keys, the ₹1 test payment page, the
  coupon, and a README for other users. Two decisions are needed from the user before building:
  which payment provider (Razorpay test mode is the obvious fit for ₹1 in India) and where key
  validation runs. A licence check living only on the user's laptop is one anyone can delete, and
  a 100%-off coupon compiled into the client is a bypass anyone reading the source will find — it
  belongs server-side and revocable.

## Gotchas that cost hours here — do not rediscover them

**The overlay page is shared.** `overlay/index.html` loads `app.js` and `ironman.js` into one
document. Three separate collisions came from this, each silent:

* An element id (`quick`) existed in both, so `getElementById` returned the wrong one and the HUD
  wrote into the conversation panel. **All HUD ids are now namespaced `im-`.**
* `const $` was declared in both. A duplicate `const` in one scope is a SyntaxError that voids the
  *entire file* — the script tag is present and every symbol in it is `undefined`. **ironman.js is
  wrapped in an IIFE.**
* `.panel` is styled in `app.css`. Scoping to `#ironmanHud` fixed the properties this file
  declares, but every property it does *not* declare still came from the other sheet — including
  the `flex-wrap` that folded the top bar onto three lines. **The HUD's class is `.frame` now, and
  `ironman.css` is fully scoped to `#ironmanHud`.**

**The overlay takes a single-instance lock.** A second launch quits immediately. To restart it you
must kill the running one first or nothing happens and the log says only "Address already in use".

**`pkill -f <pattern>` matches your own shell**, because the shell's command line contains the
pattern. It killed this session's shell twice. Match on `/proc/<pid>/comm` instead.

**Verify focus, never assume it.** `xdotool windowactivate` returns success against a full-screen
window that does not move. An early test of the editor driver typed a Command Palette shortcut and
a shell command into a full-screen video. `vscode.focus()` now checks which window actually holds
the keyboard.

**Screenshot before believing a UI works.** Every layout bug above returned `true` from the code
that was supposed to prove it.

## Conventions

Tests live in `tests/`, run with `.venv/bin/python -m pytest tests/ -q`. Commit messages explain
*why*, with measurements where measurements exist. Commit trailers:

    Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
