# Continuation prompt — paste this into Codex

Read `docs/HANDOVER-IRONMAN.md` in this repository first; it has the detail and the gotchas.
Then do the two fixes below, in order, and stop.

## Task 1 — "Jarvis, normal mode" must always return the desktop to normal

The phrase is already recognised (`jarvis/modes/ironman.asked_to_stop`). The bug is state.
`ironman._on` is in-memory and is lost on restart, while `overlay/main.js` persists the window
form — so after a restart the Iron Man frame is on screen, the backend thinks the mode is off,
and `jarvis/mode_command.py` returns `None` for "normal mode" (`if not ironman.on(): return None`),
which falls through to the model and does nothing. The user cannot get out.

Make both true:
1. The overlay never restores `ironman` as its remembered form; it starts in `pill`.
2. "Normal mode" works unconditionally — even with `ironman.on()` false it emits
   `{"kind":"mode","text":"pill"}` to `POST /emit` so a stuck frame is cleared.

## Task 2 — Iron Man mode must frame VS Code, a terminal, and ChatGPT

`ironman.lay_it_out()` currently tiles against the whole screen, so windows land *underneath* the
overlay's rails and bars, and it only moves windows that already exist — in practice just the
editor.

1. Tile to the inner rect, matching the CSS exactly: rails are `12.6vw` each side, bars are
   `4.6vh` top and bottom.
2. Launch what is missing and wait for the window before placing it:
   - ChatGPT: `google-chrome --app=https://chatgpt.com/`
   - terminal: `x-terminal-emulator`
3. Layout: editor fills the left half of the inner rect; ChatGPT top-right; terminal bottom-right.
4. Match windows by title from `wmctrl -l` — check the terminal's real title rather than assuming.

## How to verify (do not skip this)

Run it and look at the screen. Every layout bug in this feature returned success from the code
meant to prove it. Take a screenshot, compare against the reference the user gave, and say plainly
what still does not match.

`.venv/bin/python -m pytest tests/ -q` must stay green (1175 passing).

Note: the overlay holds a single-instance lock — kill the running one before starting another, or
your launch exits silently.
