"""Drive a Firefox-family browser, which does not speak the protocol everything else here does.

`browser.py` is built on the Chrome DevTools Protocol. Zen — and Firefox, Floorp, LibreWolf —
does not have it: Mozilla removed their partial CDP support in favour of WebDriver BiDi. So when
somebody moves to Zen, every click, every page read and every typed field stops working, and the
only honest thing the rest of the program could do was say so.

Marionette is the way back. It is Firefox's own automation protocol, the one geckodriver speaks
underneath every Selenium test in the world, and it is present in every build including release
ones. Probed on this machine against a throwaway profile: port 2828 answers, while the remote
debugging port serves WebDriver BiDi and 404s on CDP's own endpoints.

Why not BiDi, which is the modern answer
----------------------------------------
BiDi is a better protocol and it is where Mozilla is going. It is also a websocket, a session
negotiation and a subscription model, for a program that needs six verbs. Marionette is a length
prefix and a JSON array, it has been stable for a decade, and it is already running. When BiDi's
Python story settles this is one file to replace.

The wire
--------
Newline-free framing over TCP: `<byte length>:<json>`. The server opens with a handshake naming
its protocol version; after that every exchange is

    [0, id, "Command:Name", {params}]        ->  [1, id, error, result]

`error` is null when it worked. That is the whole protocol.

The restart it costs
--------------------
Marionette listens only when the browser was started with `--marionette`, which means one restart
— the same trade the debug port asked of Opera. Firefox restores its session by default, so it
costs a few seconds of flicker rather than your tabs, and `browser.py` never does it without
being asked.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Optional

MARIONETTE_PORT = 2828

# Long enough for a page that is still loading to finish a script, short enough that a hung tab
# does not hold the voice loop open.
TIMEOUT_S = 20.0


class MarionetteError(RuntimeError):
    """The browser answered, and the answer was no."""


class Connection:
    """One Marionette conversation. Not thread-safe; open one per task."""

    def __init__(self, port: int = MARIONETTE_PORT, timeout: float = TIMEOUT_S) -> None:
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._next_id = 0
        self._buffer = b""

    # ---------------------------------------------------------------- framing
    def _read_frame(self) -> Any:
        """One `<length>:<json>` frame.

        The length is ASCII digits before a colon, so the buffer is read until the colon is
        found and then until that many bytes have arrived. Doing it in two stages is what makes
        it safe against a frame split across TCP reads, which is normal rather than rare.
        """
        assert self._sock is not None
        while b":" not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise MarionetteError("the browser closed the connection")
            self._buffer += chunk
        head, _, rest = self._buffer.partition(b":")
        try:
            length = int(head)
        except ValueError:
            raise MarionetteError(f"unreadable frame length: {head!r}") from None
        self._buffer = rest
        while len(self._buffer) < length:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise MarionetteError("the browser closed the connection mid-frame")
            self._buffer += chunk
        body, self._buffer = self._buffer[:length], self._buffer[length:]
        return json.loads(body.decode("utf-8"))

    def _send(self, name: str, params: Optional[dict] = None) -> Any:
        assert self._sock is not None
        self._next_id += 1
        payload = json.dumps([0, self._next_id, name, params or {}]).encode("utf-8")
        self._sock.sendall(f"{len(payload)}:".encode() + payload)
        while True:
            message = self._read_frame()
            # [1, id, error, result]. Anything else is an event we did not ask for.
            if not (isinstance(message, list) and len(message) == 4 and message[0] == 1):
                continue
            if message[1] != self._next_id:
                continue
            _kind, _id, error, result = message
            if error:
                raise MarionetteError(str(error.get("message") or error))
            # WebDriver wraps every result in {"value": ...} — found by driving a real browser,
            # where GetCurrentURL came back as {'value': 'https://example.com/'} rather than the
            # string. Unwrapped once here so no caller has to know.
            if isinstance(result, dict) and set(result) == {"value"}:
                return result["value"]
            return result

    # ---------------------------------------------------------------- session
    def open(self) -> "Connection":
        self._sock = socket.create_connection(("127.0.0.1", self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)
        self._read_frame()                 # the server's handshake, which is not a reply
        self._send("WebDriver:NewSession", {"capabilities": {}})
        return self

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._send("WebDriver:DeleteSession")
        except Exception:  # noqa: BLE001 — the socket is going anyway
            pass
        try:
            self._sock.close()
        finally:
            self._sock = None

    def __enter__(self) -> "Connection":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------------------------------------------------------------- the six verbs
    def navigate(self, url: str) -> None:
        self._send("WebDriver:Navigate", {"url": url})

    def url(self) -> str:
        return str(self._send("WebDriver:GetCurrentURL") or "")

    def title(self) -> str:
        return str(self._send("WebDriver:GetTitle") or "")

    def script(self, source: str, args: Optional[list] = None) -> Any:
        """Run JavaScript in the page and return what it returns.

        `WebDriver:ExecuteScript` rather than the chrome-privileged variant: this is meant to
        read and press the page, not the browser around it, and staying in content is what keeps
        a mistake confined to a tab.
        """
        return self._send("WebDriver:ExecuteScript",
                          {"script": source, "args": args or [], "newSandbox": False})

    def text(self, limit: int = 20000) -> str:
        """What the page says, as a reader would see it."""
        got = self.script(
            "return (document.body && document.body.innerText) ? document.body.innerText : ''")
        return (got or "")[:limit]

    def tabs(self) -> list[dict]:
        """Every open tab, in the shape the rest of the program expects of a target listing."""
        handles = self._send("WebDriver:GetWindowHandles") or []
        # Asked defensively: after a tab is closed the session still points at the destroyed
        # context, and asking which window is current then fails with "Browsing context has been
        # discarded". Found by closing a tab and calling this immediately afterwards.
        try:
            current = self._send("WebDriver:GetWindowHandle")
        except MarionetteError:
            current = handles[0] if handles else None
            if current:
                self._send("WebDriver:SwitchToWindow", {"handle": current})
        out: list[dict] = []
        for handle in handles:
            try:
                self._send("WebDriver:SwitchToWindow", {"handle": handle})
                out.append({"id": handle, "type": "page",
                            "url": self.url(), "title": self.title()})
            except MarionetteError:
                continue
        if current:
            try:
                self._send("WebDriver:SwitchToWindow", {"handle": current})
            except MarionetteError:
                pass
        return out

    def switch_to(self, handle: str) -> None:
        self._send("WebDriver:SwitchToWindow", {"handle": handle})

    def close_tab(self, handle: str) -> bool:
        """Close one tab and leave the session somewhere that still exists.

        The second half is not optional. CloseWindow destroys the context the session is pointed
        at, so without switching away every later command fails with "Browsing context has been
        discarded" — which, for study mode, means the first Short it closes breaks everything
        after it. CloseWindow hands back the remaining handles, so the landing place is already
        in the reply.
        """
        try:
            self.switch_to(handle)
            remaining = self._send("WebDriver:CloseWindow") or []
        except MarionetteError:
            return False
        if remaining:
            try:
                self.switch_to(remaining[0])
            except MarionetteError:
                pass
        return True


def reachable(port: int = MARIONETTE_PORT, timeout: float = 1.0) -> bool:
    """Whether a Marionette-enabled browser is listening.

    Cheap enough to ask before every attempt, which is what lets `browser.py` decide between the
    two protocols per call rather than caching a guess made at boot.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False
