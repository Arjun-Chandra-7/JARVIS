"""The wire underneath Firefox control.

Framing is where a protocol like this actually breaks, and it breaks in production rather than in
a demo: a frame split across two TCP reads is normal, not rare, and code that assumes one recv
per message works perfectly until the page is big.

Driven against a fake socket rather than a browser, so the framing is tested without needing Zen
installed — the browser itself was verified separately and by hand.
"""

from __future__ import annotations

import json

import pytest

from jarvis.integrations import marionette
from jarvis.integrations.marionette import Connection, MarionetteError


def framed(payload) -> bytes:
    body = json.dumps(payload).encode()
    return f"{len(body)}:".encode() + body


class FakeSocket:
    """Hands back a scripted conversation, in chunks the caller does not control."""

    def __init__(self, replies, chunk=4096):
        self.stream = b"".join(framed(r) for r in replies)
        self.chunk = chunk
        self.sent: list[bytes] = []

    def recv(self, _size):
        if not self.stream:
            return b""
        out, self.stream = self.stream[:self.chunk], self.stream[self.chunk:]
        return out

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, _t):
        pass

    def close(self):
        pass


def wired(replies, chunk=4096) -> Connection:
    conn = Connection()
    conn._sock = FakeSocket(replies, chunk=chunk)
    return conn


HANDSHAKE = {"applicationType": "gecko", "marionetteProtocol": 3}


# --------------------------------------------------------------------------- framing
def test_a_reply_is_read_back():
    conn = wired([[1, 1, None, {"value": "https://example.com/"}]])
    assert conn._send("WebDriver:GetCurrentURL") == "https://example.com/"


def test_a_frame_split_across_reads_is_reassembled():
    """One byte at a time — the pathological version of what a big page does anyway."""
    conn = wired([[1, 1, None, {"value": "x" * 500}]], chunk=1)
    assert conn._send("WebDriver:GetTitle") == "x" * 500


def test_the_length_prefix_is_written_the_way_the_server_expects():
    conn = wired([[1, 1, None, {"value": None}]])
    conn._send("WebDriver:Navigate", {"url": "https://example.com/"})
    sent = conn._sock.sent[0]
    head, _, body = sent.partition(b":")
    assert int(head) == len(body), "the declared length did not match the payload"
    assert json.loads(body)[2] == "WebDriver:Navigate"


def test_an_error_becomes_an_exception_with_the_browser_s_words():
    conn = wired([[1, 1, {"message": "no such element"}, None]])
    with pytest.raises(MarionetteError, match="no such element"):
        conn._send("WebDriver:FindElement")


def test_an_event_arriving_first_is_skipped():
    """The server emits things nobody asked for; a reply is [1, id, ...] and nothing else is."""
    conn = wired([
        [0, 0, "some:event", {}],
        [1, 1, None, {"value": "ok"}],
    ])
    assert conn._send("WebDriver:GetTitle") == "ok"


def test_a_reply_to_an_older_command_is_skipped():
    conn = wired([
        [1, 99, None, {"value": "stale"}],
        [1, 1, None, {"value": "fresh"}],
    ])
    assert conn._send("WebDriver:GetTitle") == "fresh"


def test_a_closed_socket_is_an_error_not_a_hang():
    conn = wired([])
    with pytest.raises(MarionetteError, match="closed the connection"):
        conn._send("WebDriver:GetTitle")


def test_an_unreadable_length_says_so():
    conn = Connection()
    conn._sock = FakeSocket([])
    conn._sock.stream = b"notanumber:{}"
    with pytest.raises(MarionetteError, match="unreadable frame length"):
        conn._send("WebDriver:GetTitle")


# --------------------------------------------------------------------------- the value wrapper
def test_the_webdriver_value_wrapper_is_unwrapped():
    """Found by driving a real browser: GetCurrentURL comes back as {'value': '...'}, not a
    string. Unwrapped once in the framing so no caller has to know."""
    conn = wired([[1, 1, None, {"value": "Example Domain"}]])
    assert conn._send("WebDriver:GetTitle") == "Example Domain"


def test_a_dict_that_is_not_a_wrapper_is_left_alone():
    """A result with more than one key is the result, not a wrapper around one."""
    conn = wired([[1, 1, None, {"value": 1, "other": 2}]])
    assert conn._send("Whatever") == {"value": 1, "other": 2}


# --------------------------------------------------------------------------- reachability
def test_reachable_is_false_when_nothing_is_listening():
    """Asked before every attempt, which is what lets browser.py choose a protocol per call
    rather than caching a guess made at boot."""
    assert marionette.reachable(port=1, timeout=0.2) is False
