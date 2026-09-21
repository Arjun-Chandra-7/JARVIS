"""The relay that lets Jarvis drive a browser it never restarted.

Four of the ten entries in the failure journal were "Opera GX is running without control
enabled". These tests stand a real relay up on a real loopback port, connect a stand-in for the
extension and a stand-in for Jarvis, and check a CDP command makes the round trip — because the
whole design rests on the relay being indistinguishable from Chromium's own debug port, and that
is only worth believing once something has actually spoken to it.

Async tests are driven with `asyncio.run` inside ordinary test functions, the way the rest of
this suite does it, rather than adding a pytest plugin for six tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket

import pytest

from jarvis.integrations.browser_relay import Relay, drivable, tab_id_from_path

websockets = pytest.importorskip("websockets")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeExtension:
    """Stands in for the service worker: answers list-tabs, attach, detach and cdp."""

    def __init__(self, tabs):
        self.tabs = tabs
        self.attached: list[int] = []
        self.closed: list[int] = []
        self.commands: list[tuple] = []
        self.ws = None

    async def run(self, port):
        self.ws = await websockets.connect(f"ws://127.0.0.1:{port}/extension")
        async for raw in self.ws:
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "list-tabs":
                await self.ws.send(json.dumps({"type": "tabs", "tabs": self.tabs}))
            elif kind in ("attach", "detach"):
                if kind == "attach":
                    self.attached.append(msg["tabId"])
                await self.ws.send(json.dumps({"type": "result", "id": msg["id"], "result": {}}))
            elif kind == "cdp":
                self.commands.append((msg["tabId"], msg["method"], msg.get("params") or {}))
                if msg["method"] == "Boom":
                    await self.ws.send(json.dumps(
                        {"type": "result", "id": msg["id"], "error": "the tab said no"}))
                else:
                    await self.ws.send(json.dumps(
                        {"type": "result", "id": msg["id"], "result": {"echo": msg["method"]}}))
            elif kind == "close-tab":
                self.closed.append(msg["tabId"])
                self.tabs = [tab for tab in self.tabs if tab["id"] != msg["tabId"]]
                await self.ws.send(json.dumps({"type": "result", "id": msg.get("id"),
                                               "result": {"closed": True}}))
                await self.ws.send(json.dumps({"type": "tabs", "tabs": self.tabs}))

    async def emit(self, tab_id, method, params):
        await self.ws.send(json.dumps(
            {"type": "event", "tabId": tab_id, "method": method, "params": params}))


async def _until(predicate, timeout=4.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


@contextlib.asynccontextmanager
async def wired(tabs=None):
    """A running relay with a connected fake extension holding two tabs."""
    port = free_port()
    relay = Relay(port=port)
    await relay.start()
    ext = FakeExtension(tabs if tabs is not None else [
        {"id": 11, "url": "https://www.youtube.com/", "title": "YouTube"},
        {"id": 12, "url": "opera://startpage", "title": "Start"},
    ])
    task = asyncio.create_task(ext.run(port))
    assert await _until(lambda: bool(relay.targets())), "the extension never offered its tabs"
    try:
        yield relay, ext, port
    finally:
        task.cancel()
        if ext.ws is not None:
            with contextlib.suppress(Exception):
                await ext.ws.close()
        await relay.stop()


# --------------------------------------------------------------------------- pure helpers
@pytest.mark.parametrize("path,expected", [
    ("/devtools/page/42", 42),
    ("/devtools/page/42?x=1", 42),
    ("/extension", None),
    ("/devtools/page/not-a-number", None),
    ("/", None),
])
def test_the_tab_id_comes_out_of_the_path(path, expected):
    assert tab_id_from_path(path) == expected


@pytest.mark.parametrize("tab,ok", [
    ({"id": 1, "url": "https://example.com"}, True),
    ({"id": 2, "url": "opera://startpage"}, False),
    ({"id": 3, "url": "chrome://extensions"}, False),
    ({"id": 4, "url": "devtools://devtools/x"}, False),
    ({"url": "https://example.com"}, False),          # no id: nothing to attach to
    # Found by driving a real browser: the first tab it offered was Opera's own GX Corner, and
    # attaching returned "The extensions gallery cannot be scripted." It is an ordinary https
    # URL, so the scheme check above lets it straight through.
    ({"id": 5, "url": "https://gxcorner.games/"}, False),
    ({"id": 6, "url": "https://chromewebstore.google.com/category/extensions"}, False),
])
def test_only_drivable_tabs_are_offered(tab, ok):
    """The browser's own pages cannot be driven, and listing them just invites a failure."""
    assert drivable(tab) is ok


# --------------------------------------------------------------------------- the real thing
def test_targets_look_exactly_like_chromiums():
    """The whole design rests on browser.py being unable to tell the two routes apart."""
    async def run():
        async with wired() as (relay, _ext, port):
            targets = relay.targets()
            assert len(targets) == 1, "the opera:// tab should have been filtered out"
            tab = targets[0]
            assert tab["id"] == "11"
            assert tab["type"] == "page"
            assert tab["url"] == "https://www.youtube.com/"
            assert tab["webSocketDebuggerUrl"] == f"ws://127.0.0.1:{port}/devtools/page/11"

    asyncio.run(run())


def test_a_cdp_command_makes_the_round_trip():
    """Jarvis speaks plain CDP; the extension receives it aimed at the right tab."""
    async def run():
        async with wired() as (_relay, ext, port):
            async with websockets.connect(f"ws://127.0.0.1:{port}/devtools/page/11") as ws:
                await ws.send(json.dumps({"id": 1, "method": "Page.navigate",
                                          "params": {"url": "https://example.com"}}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
            assert reply["id"] == 1
            assert reply["result"] == {"echo": "Page.navigate"}
            assert ext.attached == [11], "the tab was never attached"
            assert ext.commands == [(11, "Page.navigate", {"url": "https://example.com"})]

    asyncio.run(run())


def test_an_error_comes_back_inside_the_reply():
    """A CDP client expects an error in the message, not a dropped socket — browser.py reads
    error.message and shows it to the user."""
    async def run():
        async with wired() as (_relay, _ext, port):
            async with websockets.connect(f"ws://127.0.0.1:{port}/devtools/page/11") as ws:
                await ws.send(json.dumps({"id": 7, "method": "Boom"}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
            assert reply["id"] == 7
            assert "the tab said no" in reply["error"]["message"]

    asyncio.run(run())


def test_events_reach_the_session_listening_to_that_tab():
    """browser.py waits on Page.loadEventFired; it has to travel the other way."""
    async def run():
        async with wired() as (_relay, ext, port):
            async with websockets.connect(f"ws://127.0.0.1:{port}/devtools/page/11") as ws:
                await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
                await asyncio.wait_for(ws.recv(), timeout=4)      # the reply to Page.enable
                await ext.emit(11, "Page.loadEventFired", {"timestamp": 1.0})
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
            assert event["method"] == "Page.loadEventFired"
            assert event["params"] == {"timestamp": 1.0}

    asyncio.run(run())


def test_the_http_endpoints_answer_like_a_debug_port():
    """control_ready() and _targets() reach for these before they open any socket."""
    async def run():
        import httpx

        async with wired() as (_relay, _ext, port):
            async with httpx.AsyncClient(timeout=4) as client:
                version = (await client.get(f"http://127.0.0.1:{port}/json/version")).json()
                listing = (await client.get(f"http://127.0.0.1:{port}/json")).json()
            assert version["Protocol-Version"] == "1.3"
            assert [t["id"] for t in listing] == ["11"]

    asyncio.run(run())


def test_study_mode_closes_a_new_short_through_the_extension(monkeypatch, tmp_path):
    """The relay must actually command the extension to remove a tab, not just list it."""
    async def run():
        import httpx
        from jarvis.modes import study

        monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
        study.start()
        try:
            async with wired([{"id": 30, "url": "https://example.com/", "title": "Notes"},
                              {"id": 31, "url": "https://youtube.com/shorts/abc",
                               "title": "NCERT Class 10"}]) as (relay, ext, port):
                assert await _until(lambda: ext.closed == [31])
                async with httpx.AsyncClient(timeout=4) as client:
                    blocked = (await client.get(f"http://127.0.0.1:{port}/json/close/30")).json()
                    result = (await client.get(f"http://127.0.0.1:{port}/json/close/30",
                                               headers={"X-Jarvis-Study": "close"})).json()
                assert not blocked["closed"]
                assert result["closed"]
                assert ext.closed == [31, 30]
        finally:
            study.stop()

    asyncio.run(run())


def test_a_command_with_no_extension_fails_instead_of_hanging():
    """When the extension drops, waiters are failed rather than left to time out — otherwise
    every pending call sits for the full twenty seconds."""
    async def run():
        async with wired() as (relay, ext, port):
            async with websockets.connect(f"ws://127.0.0.1:{port}/devtools/page/11") as ws:
                await ext.ws.close()
                assert await _until(lambda: not relay.connected)
                await ws.send(json.dumps({"id": 3, "method": "Page.navigate"}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
            assert "extension" in reply["error"]["message"].lower()

    asyncio.run(run())
