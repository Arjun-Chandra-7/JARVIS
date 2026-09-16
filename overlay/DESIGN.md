# The overlay

Three forms of one window. Which one is showing is `data-form` on `<body>`; what JARVIS is doing
is `data-state`. Almost everything visual keys off those two attributes, so a state change is one
assignment in `app.js` and the appearance follows from `app.css`.

    pill           a chip. Mic state, a hint of activity, nothing that competes with real work.
    conversation   the transcript and the composer.
    workspace      the same, plus tabs: Conversation, Tasks, Memory, System.

## Where the values live

`tokens.css` holds every colour, space, size, radius and duration. If a value is needed twice it
belongs there. The palette is obsidian and graphite with one ice-blue accent, spent only on state
and focus — semantic colours stay separate so that "working" never reads as "succeeded".

## What the states look like

    idle        the orb breathes slowly, so waiting looks like waiting
    listening   a halo that tracks the microphone level, readable across a desk
    speaking    rings travelling outward — the opposite motion to listening
    thinking    a steady ring, since there is no level to show
    offline     grey, and the panel edge loses its accent

The level itself arrives as `--level` on `<body>`, written once per animation frame from the same
reading the meter draws. Effects that follow the voice read that variable rather than each asking
for the number themselves.

## Rules worth keeping

- **Nothing new is always on.** Motion is peripheral: it happens at the edge of the panel, on the
  orb, or under a pointer, never in the middle of text being read.
- **An empty list is not an error.** Nothing running gets a calm dashed box, not a grey sentence.
- **A promise is not a result.** The same rule the rest of the program follows: say what happened,
  not what is about to.
- **Every animation needs its reduced-motion case**, in the block at the end of `app.css`.
- **Focus must be visible on everything.** The reset removes the browser's outline; the block near
  the end puts one back on each control that can hold focus.

## Keyboard

    /        jump to the input from anywhere
    1-4      the tabs, while the workspace is open
    Esc      clear the box, then collapse, then hide
    ← →      move along the tabs

These are listed in the tab bar, because a shortcut nobody is told about is a shortcut nobody uses.

## Judging a change

Run the overlay with `--remote-debugging-port=9333` and screenshot it over CDP. Looking at it is
the only way to tell whether a spacing change helped, and it takes a second.
