# The overlay

Three forms of one window. Which one is showing is `data-form` on `<body>`; what JARVIS is doing
is `data-state`. Almost everything visual keys off those two attributes, so a state change is one
assignment in `app.js` and the appearance follows from `app.css`.

    pill           a chip. Mic state, the last answer, a hint of activity — nothing that
                   competes with real work.
    conversation   the transcript and the composer.
    workspace      the same, plus tabs: Conversation, Tasks, Memory, System.

## Where the values live

`tokens.css` holds every colour, space, size, radius and duration. If a value is needed twice it
belongs there. The palette is obsidian and graphite with one ice-blue accent, spent only on state
and focus — semantic colours stay separate so that "working" never reads as "succeeded".

## The orb is a ring, not a disc

Seen at three times its size the orb was the heaviest thing on the bar and the emptiest: a
forty-pixel disc of near-black holding a twelve-pixel dot, reading as a hole punched in the pill
rather than as its one control. It is a ring and a core now — the same information in a tenth of
the ink, and a shape the state colour can land on without a dark plate fighting it. The room that
buys goes to the words, which are what anyone is actually reading.

`thinking` also gets a line travelling along the pill's bottom edge. "Working…" is a word that
does not change, so a pause and a hang look identical; a line that travels keeps saying it. It is
on the edge, never over the text, and it exists only while a turn is in flight — bounded by the
thing it describes rather than running for ever.

## What the states look like

    idle        the orb breathes slowly, so waiting looks like waiting — and stops after
                twenty seconds of nobody being there, because the breath is not free
    listening   a halo that tracks the microphone level, readable across a desk
    speaking    rings travelling outward — the opposite motion to listening
    thinking    a steady ring, since there is no level to show
    offline     grey, and the panel edge loses its accent

The level itself arrives as `--level` on `<body>`, written once per animation frame from the same
reading the meter draws. Effects that follow the voice read that variable rather than each asking
for the number themselves.

## The pill shows the answer

The collapsed pill carries the last thing JARVIS said, in `--text-primary` against the state
line's `--text-secondary`: the state is the label, the answer is the content, and the eye should
land on the content. It clears itself after roughly the time it takes to read, and at once when a
new question starts, since an old answer under a new question reads as a reply to it.

The pill is 280px wide and about twenty-five characters fit on a line, so it grows — upward, with
the bottom edge fixed, because the pill sits near the bottom of the screen and an edge that moves
is an edge you have to look for. Up to three lines; past that an answer belongs in the panel. The
height is measured from what was rendered, never counted from the string: the font is
proportional, and a thumbnail floated beside the text takes 46 of the 162 pixels the line had.

The temporary height is never saved. Remembered as the pill's real height, it would leave the
pill slightly taller after every answer.

## Rules worth keeping

- **Nothing new is always on**, and there is a number attached to that rule. It is a rule about
  what runs, not about what is drawn: a *static* gradient — the highlight along the top edge that
  makes the pill read as a raised object — is composited once and costs nothing. An animation
  costs, whatever it is animating. Sampling the
  compositor process of the running window, the idle orb's breath cost **27% of a core,
  continuously**, to move a ten-pixel dot. None of the usual remedies touched it — promoting the
  element to its own layer, dropping to opacity alone, slowing it to twelve seconds, all within a
  point of each other; only stopping stopped it. A transparent always-on-top window is
  recomposited against whatever is behind it on every frame, so *any* continuous animation costs
  about the same, and a slower one costs the same because it still interpolates at sixty frames a
  second. Anything that runs for ever needs a reason and a way to stop. Measure it on the real
  window before assuming it is cheap.
- Motion is peripheral: it happens at the edge of the panel, on the orb, or under a pointer,
  never in the middle of text being read.
- **An empty list is not an error.** Nothing running gets a calm dashed box, not a grey sentence.
- **A promise is not a result.** The same rule the rest of the program follows: say what happened,
  not what is about to.
- **Every animation needs its reduced-motion case**, in the block at the end of `app.css`.
- **Focus must be visible on everything.** The reset removes the browser's outline; the block near
  the end puts one back on each control that can hold focus.
- **A path in a reply is not a file you may open.** Replies are text a language model wrote.
  Pictures render only from the folder JARVIS writes them to, and opening one goes through its own
  IPC channel that resolves the path and checks it is still inside that folder — never by widening
  `openExternal`, which is for the web.
- **A message body is rewritten while it is revealed.** Anything attached to a reply — a picture,
  a control — goes on the message element, not inside `.msg-body`, or the typing animation throws
  it away on the next frame.

## The command palette

`Ctrl+K`. A voice assistant you have to already know the words for is a menu with the menu
missing: the palette is the list of what JARVIS can be told to do, filtered as you type and run
from the keyboard.

Every entry reaches something that already exists — a capture endpoint, a control endpoint, a tab
in this window, or a sentence the backend's own command layer parses. An entry with no working
destination does not belong in it. The "Ask" group is whatever `/suggestions` is offering, so the
list grows when the backend learns something rather than when `app.js` is edited.

The filter is a scored subsequence match, and consecutive letters are weighted hard. Without that,
`irn` put "Cancel what is running" — i·s, r·unning, ru·n·ning — above "Iron Man mode", which starts
with the letters in order.

## What is playing

A strip, not a player: cover, two lines, three controls, posting to `/spotify/control`. It is there
only while something is loaded — a paused track counts, because that is exactly when the play
button is wanted — and the poll behind it starts and stops with the panel. The cover loads only
from an `https` URL: the address comes from whatever happens to be playing, and a renderer with
this much reach should not be talked into fetching `file://` by a track's metadata.

## A measure to read against

The panel is as wide as the window and the window can be the whole desktop. At that width the
transcript put your words against one edge and JARVIS's against the other with a metre of nothing
between them, and the now-playing strip had its title and its skip button eighteen hundred pixels
apart. Content is held to a measure and centred in whatever room there is; the chrome — the pill
bar, the tabs — still spans, because that is the window. The panel may be any width. The reading
column is not.

## Keyboard

    Ctrl+K   the command palette
    /        jump to the input from anywhere
    Alt+1-4  the tabs, while the workspace is open
    Esc      close the palette, then clear the box, then collapse, then hide
    ← →      move along the tabs

These are listed in the tab bar, because a shortcut nobody is told about is a shortcut nobody uses.

## Judging a change

Run the overlay with `--remote-debugging-port=9333` and screenshot it over CDP. Looking at it is
the only way to tell whether a spacing change helped, and it takes a second.
