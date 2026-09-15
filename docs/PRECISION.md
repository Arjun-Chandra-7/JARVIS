# Precise control of the computer

Jarvis can drive applications and web pages exactly, rather than guessing at pixels.

## What you can say

```
open opera gx                    → launches the application
open netflix                     → opens the site in Opera GX
select the profile of arjun      → clicks that profile
open f.r.i.e.n.d.s               → searches for it on the site you are already on
click the first episode          → clicks it
what can I click on this page    → lists everything clickable
pause the video                  → sends space to the page
make it fullscreen               → sends f
go back                          → browser history back
set the volume to 35             → works
set the brightness to 60         → needs one permission fix, see below
```

Each step acts on whatever the page became after the previous one, because every call re-reads the
live page.

## Two kinds of clicking, and why

| | How it works | Use for |
| --- | --- | --- |
| `browser_click` | Finds the element whose visible text matches, clicks its centre with a real input event | Anything inside a web page |
| `find_and_click` | Screenshots the screen and asks a vision model where the thing is | Native application windows |

The browser path is exact and costs nothing; the vision path is approximate and costs an API call,
so it is scoped to windows that cannot be inspected any other way. Clicks are dispatched through
Chromium's input pipeline, so `event.isTrusted` is true and sites that ignore synthetic clicks
still respond.

## Enabling browser control

Precise control needs Opera GX started with its DevTools port open. Jarvis does that itself when
it starts the browser. If Opera GX is **already** running without it, Jarvis will say so and can
open pages but not click inside them; say **"restart Opera with control"** and it will restart it
(which closes your current tabs, so it asks first).

Your normal Opera GX profile is used, so logins, watch history and profiles are your own.

**What this costs:** the DevTools port listens on `127.0.0.1:9333`. Any program already running as
you can use it to drive the browser. That is the same trust boundary as your own shell, but it is
a real widening of it — set `JARVIS_BROWSER_CONTROL=0` to turn the whole thing off and fall back
to simply opening URLs.

## Opening applications

Spoken names are resolved against the installed `.desktop` entries — the same list your app
launcher shows — scored so a specific name wins: "opera gx" resolves to Opera GX, not to Opera.
"vs code", "files", "settings" and similar have aliases. Plain "opera" means Opera GX on this
machine.

Asked to open something that is a website rather than a program ("open netflix"), it says so and
hands over to the browser instead of failing.

## Hardware

| | State |
| --- | --- |
| Volume | Works (`wpctl`) |
| Media keys, play/pause | Works (`playerctl`) |
| Keyboard, mouse, typing | Works (`ydotool`, with `ydotoold` running) |
| Keyboard backlight | Works (GNOME D-Bus) |
| **Screen brightness** | **Blocked — see below** |
| Do not disturb, lock screen | Works |

### Screen brightness needs one command

The backlight on this laptop (`/sys/class/backlight/nvidia_0`) is owned by the `video` group, and
this account is not in it. GNOME does not expose a screen-brightness D-Bus interface here either —
only the keyboard one — so every path requires that permission.

```bash
sudo usermod -aG video $USER     # then log out and back in
```

Until then Jarvis reads the brightness correctly and tells you exactly this instead of pretending
it changed something. (It used to reply "done." either way.)

## Configuration

```bash
JARVIS_BROWSER=opera-gx          # any browser on PATH
JARVIS_BROWSER_CONTROL=0         # disable precise control entirely
JARVIS_BROWSER_DEBUG_PORT=9333   # the loopback DevTools port
JARVIS_TEMPERATURE=0.15          # lower is more literal about which tool to use
```

## What was verified

Against a real Opera GX and a real website (`tests/test_browser_control.py` covers the decisions;
this chain was run by hand):

1. Open a site by name → Wikipedia loaded.
2. Read the page → the real clickable items came back.
3. "Open the theory of relativity" while already on Wikipedia → searched **Wikipedia**, not Google,
   and returned its real suggestions.
4. Click the result by its visible text → landed on the article.
5. Key presses and history-back → both worked.
6. Clicks arrive with `isTrusted = true`.

Not verified: Netflix specifically, because that needs your logged-in profile and restarting your
browser. The mechanism is the same one proven above; the first real run is the test.
