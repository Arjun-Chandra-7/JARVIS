"""Control the browser that is already open, instead of the one we had to restart.

Four of the ten entries in Jarvis's failure journal are the same sentence:

    Opera GX is running without control enabled, so I can only open pages, not click inside
    them. Restarting it with control on will close your current tabs.

So "play the latest MKBHD video", "find the Play button and hit it" and "open ChatGPT on Opera"
all died against a browser that was running perfectly well — just not started with
`--remote-debugging-port`. The remedy costs the tabs you have open, so it never gets taken, so it
fails again the next day.

An extension does not need the debug port. It runs *inside* the browser with declared
permissions, the way a password manager does, and `chrome.debugger` gives it the same Chrome
DevTools Protocol the port would have exposed. This module is the other half: a small loopback
server the extension dials out to, presenting the extension's tabs in exactly the shape
Chromium's own debug port presents them.

That shape is the whole point. `_targets()` and `_Session` in browser.py already speak CDP over a
websocket; pointing them at this relay instead of at port 9333 needs no change to either. This is
a different way into the same protocol, not a second protocol.

    Jarvis  ──ws──>  relay  ──ws──>  extension  ──chrome.debugger──>  your open tab

Loopback only, and deliberately: anything that can reach this socket can drive a browser logged
into everything you are logged into.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

# Distinct from browser.DEBUG_PORT (9333). Both can be live at once — a browser started with the
# port *and* the extension loaded — and the caller prefers the native one.
RELAY_PORT = int(os.environ.get("JARVIS_BROWSER_RELAY_PORT", "9334"))

# The extension connects here; Jarvis connects to /devtools/page/<tabId>.
EXTENSION_PATH = "/extension"

# How long to wait for the extension to answer one command. The browser is usually quick; this
# guards against an extension the browser has suspended.
CALL_TIMEOUT_S = 20.0


class Relay:
    """One extension, however many Jarvis sessions."""

    def __init__(self, port: int = RELAY_PORT) -> None:
        self.port = port
        self._extension = None                      # the extension's websocket, when connected
        self._tabs: list[dict] = []
        self._pending: dict[int, asyncio.Future] = {}
        self._sessions: dict[int, set] = {}         # tabId -> sockets listening to it
        self._next_id = 0
        self._server = None

    # ---------------------------------------------------------------- state
    @property
    def connected(self) -> bool:
        return self._extension is not None

    def targets(self) -> list[dict]:
        """The tab list, in the shape Chromium's /json returns.

        Same keys, same meanings — including `webSocketDebuggerUrl`, which points back here — so
        the code that reads Chromium's own listing never learns a second format.
        """
        out = []
        for tab in self._tabs:
            tab_id = tab.get("id")
            out.append({
                "id": str(tab_id),
                "type": "page",
                "title": tab.get("title", ""),
                "url": tab.get("url", ""),
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/page/{tab_id}",
            })
        return out

    # ---------------------------------------------------------------- extension side
    async def _handle_extension(self, ws) -> None:
        if self._extension is not None:
            # One browser, one extension. A second connection is a stale socket from a reload.
            try:
                await self._extension.close()
            except Exception:  # noqa: BLE001
                pass
        self._extension = ws
        try:
            await self._ask_for_tabs()
            async for raw in ws:
                await self._from_extension(raw)
        except Exception:  # noqa: BLE001 — a dropped extension is normal, not an error
            pass
        finally:
            if self._extension is ws:
                self._extension = None
                self._tabs = []
                # Nothing is coming back for these now. Fail the waiters rather than leave them
                # to sit out their timeout.
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("the browser extension disconnected"))
                self._pending.clear()

    async def _from_extension(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        kind = msg.get("type")
        if kind == "tabs":
            self._tabs = [t for t in msg.get("tabs", []) if drivable(t)]
        elif kind == "result":
            fut = self._pending.pop(msg.get("id"), None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif kind == "event":
            await self._fan_out(msg)

    async def _fan_out(self, msg: dict) -> None:
        """A CDP event from one tab goes to whoever is listening to that tab."""
        payload = json.dumps({"method": msg.get("method"), "params": msg.get("params", {})})
        listeners = self._sessions.get(msg.get("tabId"))
        for ws in list(listeners or ()):
            try:
                await ws.send(payload)
            except Exception:  # noqa: BLE001
                listeners.discard(ws)

    async def _ask_for_tabs(self) -> None:
        if self._extension is not None:
            await self._extension.send(json.dumps({"type": "list-tabs"}))

    async def close_tab(self, tab_id: int) -> bool:
        """Close a tab. Not a CDP command — see the extension for why it is not."""
        try:
            reply = await self._to_extension({"type": "close", "tabId": int(tab_id)})
        except Exception:  # noqa: BLE001
            return False
        if reply.get("error"):
            return False
        self._tabs = [t for t in self._tabs if t.get("id") != int(tab_id)]
        return True

    async def _to_extension(self, body: dict) -> dict:
        if self._extension is None:
            raise ConnectionError("no browser extension is connected")
        self._next_id += 1
        call_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[call_id] = fut
        await self._extension.send(json.dumps(dict(body, id=call_id)))
        try:
            return await asyncio.wait_for(fut, timeout=CALL_TIMEOUT_S)
        finally:
            self._pending.pop(call_id, None)

    # ---------------------------------------------------------------- Jarvis side
    async def _handle_page(self, ws, tab_id: int) -> None:
        """One CDP conversation with one tab, spoken exactly as Chromium would speak it."""
        self._sessions.setdefault(tab_id, set()).add(ws)
        try:
            await self._to_extension({"type": "attach", "tabId": tab_id})
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                await self._relay_command(ws, tab_id, msg)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._sessions.get(tab_id, set()).discard(ws)
            if not self._sessions.get(tab_id):
                self._sessions.pop(tab_id, None)
                try:
                    await self._to_extension({"type": "detach", "tabId": tab_id})
                except Exception:  # noqa: BLE001
                    pass

    async def _relay_command(self, ws, tab_id: int, msg: dict) -> None:
        call_id = msg.get("id")
        try:
            reply = await self._to_extension({
                "type": "cdp", "tabId": tab_id,
                "method": msg.get("method"), "params": msg.get("params") or {},
            })
        except Exception as exc:  # noqa: BLE001
            # A CDP client expects an error inside the reply, not a dropped socket. browser.py
            # shows `error.message` to the user, and "the extension went away" reads better
            # than a timeout with nothing attached to it.
            await _send(ws, {"id": call_id, "error": {"message": str(exc)}})
            return
        if reply.get("error"):
            await _send(ws, {"id": call_id, "error": {"message": str(reply["error"])}})
        else:
            await _send(ws, {"id": call_id, "result": reply.get("result") or {}})

    # ---------------------------------------------------------------- serving
    async def start(self) -> None:
        import websockets

        async def route(ws):
            path = path_of(ws)
            if path == EXTENSION_PATH:
                await self._handle_extension(ws)
                return
            tab_id = tab_id_from_path(path)
            if tab_id is None:
                await ws.close(code=1008, reason="unknown path")
                return
            await self._handle_page(ws, tab_id)

        self._server = await websockets.serve(
            route, "127.0.0.1", self.port,           # loopback only, never 0.0.0.0
            process_request=self._http, max_size=30_000_000,
        )

    async def _http(self, connection, request):
        """Answer the two plain HTTP endpoints a CDP client looks for before it opens a socket."""
        path = getattr(request, "path", "")
        if path.startswith("/json/version"):
            return _json_response({
                "Browser": "Jarvis-Relay/1.0",
                "Protocol-Version": "1.3",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}{EXTENSION_PATH}",
            })
        if path.rstrip("/") == "/json" or path.startswith("/json/list"):
            return _json_response(self.targets())
        return None                                  # anything else continues to the websocket

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


# --------------------------------------------------------------------------- helpers
# Ordinary https pages that Chromium refuses to let any extension script. Found by driving a real
# browser rather than by reading docs: the first tab offered was Opera's own GX Corner, and the
# attach came back "The extensions gallery cannot be scripted." These are not opera:// URLs, so
# the scheme check below lets them through, and every attempt on one fails the same way.
_UNSCRIPTABLE_HOSTS = (
    "gxcorner.games",
    "addons.opera.com",
    "chrome.google.com/webstore",
    "chromewebstore.google.com",
)


def drivable(tab: dict) -> bool:
    """Tabs worth offering.

    A tab that can never be driven does not belong in the list: offering one only moves the
    failure to the moment somebody tries to use it.
    """
    url = (tab.get("url") or "").lower()
    if tab.get("id") is None:
        return False
    if url.startswith(("chrome://", "opera://", "devtools://", "chrome-extension://", "about:")):
        return False
    return not any(host in url for host in _UNSCRIPTABLE_HOSTS)


def tab_id_from_path(path: str) -> Optional[int]:
    """The tab id out of /devtools/page/<id>, or None when the path is not one of ours."""
    prefix = "/devtools/page/"
    if not path.startswith(prefix):
        return None
    try:
        return int(path[len(prefix):].split("?")[0])
    except ValueError:
        return None


def path_of(ws) -> str:
    request = getattr(ws, "request", None)
    return getattr(request, "path", "") or getattr(ws, "path", "") or ""


def _json_response(payload):
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    body = json.dumps(payload).encode()
    return Response(200, "OK", Headers({
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }), body)


async def _send(ws, payload: dict) -> None:
    try:
        await ws.send(json.dumps(payload))
    except Exception:  # noqa: BLE001
        pass
