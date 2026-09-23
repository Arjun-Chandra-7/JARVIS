"""One way to run a script in the page in front of you, whichever browser it is.

Chromium speaks CDP (browser.py); Firefox and Zen speak Marionette (marionette.py). Everything
built on a page — the DOM provider, the YouTube adapter — needs only ``run(body, args)``, so it is
written once. ``body`` is a JavaScript function body: it reads its inputs from ``arguments``
(passed as data, never formatted into the source) and may return a value or a promise.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional, Protocol


class Page(Protocol):
    kind: str

    async def run(self, body: str, args: Optional[list] = None, timeout: float = 20.0) -> Any: ...


class MarionettePage:
    kind = "marionette"

    def __init__(self, port: Optional[int] = None, url: Optional[str] = None) -> None:
        from ..integrations import marionette
        self.port = port or marionette.MARIONETTE_PORT
        # A specific tab, by its address. Not by handle: every call opens a new Marionette
        # session and the handles do not survive it ("Unable to locate window", found live).
        self.url = url

    async def run(self, body: str, args: Optional[list] = None, timeout: float = 20.0) -> Any:
        from ..integrations import marionette

        # ExecuteScript runs the body as a plain function; wrapped so bodies may use await, the
        # same as under CDP. WebDriver waits for the returned promise.
        wrapped = f"return (async function(){{{body}\n}}).apply(null, arguments);"

        def work():
            with marionette.Connection(port=self.port, timeout=timeout) as conn:
                if self.url and not _switch_to_url(conn, self.url):
                    raise RuntimeError("the video tab was closed")
                return conn.script(wrapped, args or [])
        return await asyncio.to_thread(work)


def _same_video(a: str, b: str) -> bool:
    """Same tab, allowing for the &t= that moves as the video plays."""
    strip = lambda u: re.sub(r"[&?]t=\d+s?", "", (u or "").split("#")[0])  # noqa: E731
    return strip(a) == strip(b)


def _switch_to_url(conn, url: str) -> bool:
    for handle in conn._send("WebDriver:GetWindowHandles") or []:
        try:
            # focus=False: read that tab without bringing it to the front.
            conn._send("WebDriver:SwitchToWindow", {"handle": handle, "focus": False})
            if _same_video(conn.url(), url):
                return True
        except Exception:  # noqa: BLE001 — a tab that will not answer is not the one
            continue
    return False


# What a tab is doing, cheaply: enough to tell the video being watched from one left in a tab.
_TAB_STATE = ("const v=document.querySelector('video');"
              "return {url: location.href, visible: document.visibilityState === 'visible',"
              " playing: !!(v && !v.paused && !v.ended), at: v ? v.currentTime : 0};")
_WATCH = ("youtube.com/watch", "youtube.com/shorts/", "youtube.com/live/", "youtu.be/")


def _rank(state: dict) -> tuple:
    return (bool(state.get("visible")), bool(state.get("playing")), float(state.get("at") or 0) > 0)


def video_page() -> Optional[Page]:
    """The tab with the video the person is watching (see ``find_video_page``)."""
    return find_video_page()


def find_video_page() -> Optional[Page]:
    """The tab with the video the person is watching, in whichever browser has it.

    ``active_page`` gives the tab a new Marionette session lands on — found live to be a ChatGPT
    tab while the lecture played in another, so every "what did he just say" was answered
    "that page isn't a YouTube video". A question about the video looks for the video: a visible
    YouTube tab first, then one that is playing, then one paused part-way, then any. Falls back
    to ``active_page`` when no tab has one.
    """
    from ..integrations import marionette

    try:
        if marionette.reachable():
            best, best_rank = None, None
            with marionette.Connection(timeout=10) as conn:
                current = conn._send("WebDriver:GetWindowHandle")
                for handle in conn._send("WebDriver:GetWindowHandles") or []:
                    try:
                        conn._send("WebDriver:SwitchToWindow", {"handle": handle, "focus": False})
                        state = conn.script(_TAB_STATE) or {}
                    except Exception:  # noqa: BLE001 — a tab that will not answer is not the one
                        continue
                    if not any(w in str(state.get("url", "")) for w in _WATCH):
                        continue
                    rank = _rank(state)
                    if best_rank is None or rank > best_rank:
                        best, best_rank = str(state.get("url")), rank
                if current:
                    try:
                        conn._send("WebDriver:SwitchToWindow", {"handle": current, "focus": False})
                    except Exception:  # noqa: BLE001
                        pass
            if best:
                return MarionettePage(url=best)
    except Exception:  # noqa: BLE001 — fall through to the ordinary page
        pass
    return active_page()


class CdpPage:
    kind = "cdp"

    async def run(self, body: str, args: Optional[list] = None, timeout: float = 20.0) -> Any:
        from ..integrations import browser

        expression = f"(async function(){{{body}\n}}).apply(null, {json.dumps(args or [])})"

        async def go(session):
            return await session.js(expression, timeout=timeout)
        result = await browser._with_page(go, timeout=timeout + 2)
        if isinstance(result, dict) and result.get("ok") is False and "error" in result:
            raise RuntimeError(result["error"])
        return result


def active_page() -> Optional[Page]:
    """The drivable page in front of the person, or None when no browser can be driven."""
    from ..integrations import browser, marionette, web_browser

    try:
        if web_browser.family() == "firefox" and marionette.reachable():
            return MarionettePage()
        if marionette.reachable():
            return MarionettePage()
        if browser.control_ready():
            return CdpPage()
    except Exception:  # noqa: BLE001 — no browser is an answer, not an error
        return None
    return None


def why_no_page() -> str:
    from ..integrations import web_browser
    try:
        family = web_browser.family()
    except Exception:  # noqa: BLE001
        family = ""
    if family == "firefox":
        return ("I can't read the browser — Zen and Firefox have to be started with control. "
                'Say "restart the browser with control" and I\'ll do it; your tabs come back.')
    return ("I can't read the browser — it needs its debug port or the Jarvis extension. "
            'Say "restart the browser with control".')
