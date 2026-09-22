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

*Driving* — clicking inside a page, reading it, typing into it — needs the DevTools protocol, and
that is a Chromium thing. A Firefox-family browser (Zen, Floorp, LibreWolf, Firefox) does not
speak it, so `can_be_driven()` answers honestly instead of letting a caller discover it halfway
through a click.

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
    """Whether Jarvis can click and read inside this browser's pages.

    Only Chromium's family speaks the DevTools protocol that `browser.py` is built on. Firefox
    removed its own partial CDP support in favour of WebDriver BiDi, which is a different
    protocol and not one anything here talks yet.
    """
    return family(name) == "chromium"


def why_not_drivable(name: Optional[str] = None) -> str:
    """A sentence for the user when a page cannot be driven, naming the actual reason."""
    if can_be_driven(name):
        return ""
    which = name if name is not None else preferred()
    if family(which) == "firefox":
        return (f"{_spoken(which)} is a Firefox-family browser, so I can open pages in it but "
                "not click inside them — that needs a Chromium browser.")
    return f"I can open pages in {_spoken(which)} but I can't drive them."


def _spoken(name: str) -> str:
    """A browser id said the way a person says it."""
    lowered = (name or "").lower()
    for word, said in (("zen", "Zen"), ("firefox", "Firefox"), ("brave", "Brave"),
                       ("chromium", "Chromium"), ("chrome", "Chrome"),
                       ("opera", "Opera"), ("vivaldi", "Vivaldi"), ("edge", "Edge")):
        if word in lowered:
            return said
    return name or "your browser"
