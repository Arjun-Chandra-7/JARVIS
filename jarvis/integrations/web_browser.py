"""Which browser to open a page in, decided once.

"opera" was written into a dozen call sites — the LinkedIn console, the study-mode ChatGPT tab,
Iron Man's layout, the coding setup. Changing browsers therefore did not change browsers: pages
kept being sent to one that was no longer the default, and the LinkedIn dashboard quietly stopped
appearing because the call that opened it had a browser's name baked into it.

So the choice lives here, and everywhere else asks.

Opening a page and driving a page are different questions
---------------------------------------------------------
Worth separating, because the answers differ and conflating them is what made this brittle.

*Opening* works with any browser: hand the URL to the desktop and the user's default takes it.

*Driving* — clicking inside a page, reading it, typing into it — needs an automation protocol,
and the two families have different ones. Chromium has the DevTools protocol; Firefox has
Marionette. Both are supported, so `can_be_driven()` is about whether Jarvis speaks this
browser's protocol at all — not about which one it is.

What differs is the *condition*. Chromium needs its debug port or the extension; Firefox needs to
have been started with `--marionette`. Neither is on by default, which is why `why_not_drivable()`
names the specific remedy rather than shrugging.

The order of preference
-----------------------
1. `JARVIS_BROWSER` when it is set — an explicit choice always wins.
2. The desktop's own default, as `xdg-settings` reports it. If somebody changed their browser,
   this is where they said so.
3. Whatever is actually installed, from a short list.

There is no hard-coded favourite at the end of that chain, which is the whole point.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from typing import Optional

# Desktop-entry stems mapped to the family they belong to. Only the family matters here: it
# decides whether a page can be driven, not how it is opened.
CHROMIUM_FAMILY = (
    "chrome", "chromium", "brave", "opera", "vivaldi", "edge", "arc", "thorium",
)
FIREFOX_FAMILY = ("firefox", "zen", "floorp", "librewolf", "waterfox", "mullvad")

# Tried in order when nothing else has answered.
KNOWN_BROWSERS = (
    "zen", "zen-browser", "firefox", "brave-browser", "google-chrome", "chromium",
    "opera-gx", "opera", "vivaldi",
)


def _xdg_default() -> str:
    """The desktop's default browser, as its .desktop stem. Empty when it cannot be asked."""
    try:
        out = subprocess.run(["xdg-settings", "get", "default-web-browser"],
                             capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip().removesuffix(".desktop")


@lru_cache(maxsize=1)
def preferred() -> str:
    """The browser pages should open in. A desktop-entry id or an executable name."""
    chosen = os.environ.get("JARVIS_BROWSER", "").strip()
    if chosen:
        return chosen
    default = _xdg_default()
    if default:
        return default
    for name in KNOWN_BROWSERS:
        if shutil.which(name):
            return name
    return ""


def forget() -> None:
    """Ask again next time — for tests, and for somebody who has just switched browsers."""
    preferred.cache_clear()


def family(name: Optional[str] = None) -> str:
    """"chromium", "firefox", or "" when it is neither or unknown."""
    lowered = (name if name is not None else preferred()).lower()
    if not lowered:
        return ""
    # Checked against the whole id, because a flatpak one looks like
    # "app.zen_browser.zen" rather than "zen".
    if any(part in lowered for part in FIREFOX_FAMILY):
        return "firefox"
    if any(part in lowered for part in CHROMIUM_FAMILY):
        return "chromium"
    return ""


def can_be_driven(name: Optional[str] = None) -> bool:
    """Whether Jarvis speaks this browser's automation protocol at all.

    Both families are supported now — Chromium over the DevTools protocol, Firefox over
    Marionette. This says nothing about whether the browser currently *has* automation switched
    on; that is a live question and `browser.control_ready()` answers it.
    """
    return family(name) in ("chromium", "firefox")


def why_not_drivable(name: Optional[str] = None) -> str:
    """A sentence for the user when a page cannot be driven, naming the actual reason."""
    if can_be_driven(name):
        return ""
    which = name if name is not None else preferred()
    return (f"I can open pages in {_spoken(which)} but I don't speak its automation protocol — "
            "I know Chromium's DevTools and Firefox's Marionette.")


def how_to_enable(name: Optional[str] = None) -> str:
    """What would have to happen for this browser to be driveable, in a sentence.

    Separate from why_not_drivable because they answer different questions: one is "I cannot
    ever", the other is "I could, but it is switched off".
    """
    which = name if name is not None else preferred()
    kind = family(which)
    if kind == "firefox":
        return (f"{_spoken(which)} needs restarting with automation enabled. It restores your "
                "tabs, so it costs a few seconds rather than your session.")
    if kind == "chromium":
        return (f"{_spoken(which)} needs either the Jarvis extension loaded once, or a restart "
                "with its debug port open.")
    return ""


def _spoken(name: str) -> str:
    """A browser id said the way a person says it."""
    lowered = (name or "").lower()
    for word, said in (("zen", "Zen"), ("firefox", "Firefox"), ("brave", "Brave"),
                       ("chromium", "Chromium"), ("chrome", "Chrome"),
                       ("opera", "Opera"), ("vivaldi", "Vivaldi"), ("edge", "Edge")):
        if word in lowered:
            return said
    return name or "your browser"
