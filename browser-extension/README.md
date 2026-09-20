# Jarvis browser control

Lets Jarvis click inside the tabs you already have open, without restarting the browser.

## Why this exists

Jarvis drives the browser over the Chrome DevTools Protocol, which normally means starting it
with `--remote-debugging-port`. If the browser is already running without that flag, Jarvis can
open pages but not touch anything on them, and the only fix is a restart that costs you every
open tab.

Nobody takes that trade. It is the most common failure in Jarvis's own journal — four of ten
recorded failures are the same sentence, killing "play the latest MKBHD video", "find the Play
button and hit it" and "open ChatGPT on Opera".

An extension does not need the port. It runs inside the browser with declared permissions, the
way a password manager does, and `chrome.debugger` is the same protocol the port would have
exposed.

## Install it once

1. Open `opera://extensions` (or `chrome://extensions` on a Chromium-family browser).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and choose this `browser-extension/` folder.

That is the whole install. It survives browser restarts. It does not survive being removed.

You will see the browser's "being debugged by Jarvis" banner on a tab while Jarvis is acting on
it, and not otherwise — that banner is the browser telling you the truth, and it is worth
leaving on.

## Checking it worked

With Jarvis running:

    curl -s http://127.0.0.1:9334/json | python3 -m json.tool

You should get your open tabs. An empty list means the extension is loaded but has not connected
— check that Jarvis is running, since the extension dials out to it rather than the other way
round.

## How it fits together

    Jarvis  ──ws──>  relay (127.0.0.1:9334)  ──ws──>  extension  ──chrome.debugger──>  your tab

The relay presents the extension's tabs in exactly the shape Chromium's debug port presents them,
so nothing above it knows which route it is on. Jarvis prefers the native port when one is
available — it is the browser's own, needs no third process, and can see targets an extension
cannot — and falls back to this.

## What you are trusting

This is worth being plain about, because the convenience is real and so is the exposure.

- The extension can drive any tab, in a browser logged into everything you are logged into.
- The relay listens on loopback only, never on `0.0.0.0`. Anything running as you on this
  machine can reach it, which is the same bar as the debug port it replaces.
- Load it unpacked from a path you control. Do not install this, or anything like it, from a
  store listing.

If that is more than you want, delete the extension and Jarvis falls back to asking before it
restarts the browser, exactly as before.
