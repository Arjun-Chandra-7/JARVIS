"""One way to run a script in the page in front of you, whichever browser it is.

Chromium speaks CDP (browser.py); Firefox and Zen speak Marionette (marionette.py). Everything
built on a page — the DOM provider, the YouTube adapter — needs only ``run(body, args)``, so it is
written once. ``body`` is a JavaScript function body: it reads its inputs from ``arguments``
(passed as data, never formatted into the source) and may return a value or a promise.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional, Protocol


class Page(Protocol):
    kind: str

    async def run(self, body: str, args: Optional[list] = None, timeout: float = 20.0) -> Any: ...


class MarionettePage:
    kind = "marionette"

    def __init__(self, port: Optional[int] = None) -> None:
        from ..integrations import marionette
        self.port = port or marionette.MARIONETTE_PORT

    async def run(self, body: str, args: Optional[list] = None, timeout: float = 20.0) -> Any:
        from ..integrations import marionette

        # ExecuteScript runs the body as a plain function; wrapped so bodies may use await, the
        # same as under CDP. WebDriver waits for the returned promise.
        wrapped = f"return (async function(){{{body}\n}}).apply(null, arguments);"

        def work():
            with marionette.Connection(port=self.port, timeout=timeout) as conn:
                return conn.script(wrapped, args or [])
        return await asyncio.to_thread(work)


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
