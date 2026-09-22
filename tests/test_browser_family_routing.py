"""Choosing a protocol per call, without a browser in the room.

The live behaviour was verified by driving a real Zen. What is pinned here is the decision — that
a Firefox browser takes the Marionette path and a Chromium one does not — because that decision
is invisible when it goes wrong: the CDP call simply times out and everything above reports a
page that would not respond.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.integrations import browser


@pytest.fixture
def firefox(monkeypatch):
    """A driveable Firefox in front of us."""
    from jarvis.integrations import marionette, web_browser

    monkeypatch.setattr(web_browser, "family", lambda *_a, **_k: "firefox")
    monkeypatch.setattr(marionette, "reachable", lambda *_a, **_k: True)
    return monkeypatch


@pytest.fixture
def chromium(monkeypatch):
    from jarvis.integrations import marionette, web_browser

    monkeypatch.setattr(web_browser, "family", lambda *_a, **_k: "chromium")
    monkeypatch.setattr(marionette, "reachable", lambda *_a, **_k: False)
    return monkeypatch


class FakeConn:
    """Enough of a Marionette connection to see which verbs were asked for."""

    def __init__(self):
        self.calls: list[str] = []

    def url(self):
        self.calls.append("url")
        return "https://example.com/"

    def title(self):
        self.calls.append("title")
        return "Example"

    def tabs(self):
        self.calls.append("tabs")
        return [{"id": "w1", "type": "page", "url": "https://example.com/", "title": "Example"}]

    def close_tab(self, handle):
        self.calls.append(f"close:{handle}")
        return True

    def script(self, *_a, **_k):
        self.calls.append("script")
        return ""


def through(monkeypatch, conn):
    async def fake(fn):
        return fn(conn)

    monkeypatch.setattr(browser, "_through_marionette", fake)


# --------------------------------------------------------------------------- the decision
def test_a_driveable_firefox_counts_as_control(firefox):
    assert browser.control_ready() is True


def test_firefox_with_no_automation_does_not(monkeypatch):
    from jarvis.integrations import marionette, web_browser

    monkeypatch.setattr(web_browser, "family", lambda *_a, **_k: "firefox")
    monkeypatch.setattr(marionette, "reachable", lambda *_a, **_k: False)
    monkeypatch.setattr(browser, "native_ready", lambda timeout=1.5: False)
    monkeypatch.setattr(browser, "relay_ready", lambda: False)
    assert browser.control_ready() is False


def test_the_choice_is_made_per_call_not_cached(monkeypatch):
    """A browser can be restarted into automation halfway through a session, and nothing should
    have to be told."""
    from jarvis.integrations import marionette, web_browser

    monkeypatch.setattr(web_browser, "family", lambda *_a, **_k: "firefox")
    answers = iter([False, True])
    monkeypatch.setattr(marionette, "reachable", lambda *_a, **_k: next(answers))
    assert browser._firefox_now() is False
    assert browser._firefox_now() is True


# --------------------------------------------------------------------------- the four verbs
def test_current_page_asks_marionette(firefox, monkeypatch):
    conn = FakeConn()
    through(monkeypatch, conn)
    page = asyncio.run(browser.current_page())
    assert page["url"] == "https://example.com/"
    assert "url" in conn.calls


def test_tab_listing_asks_marionette(firefox, monkeypatch):
    conn = FakeConn()
    through(monkeypatch, conn)
    targets = asyncio.run(browser._targets())
    assert [t["id"] for t in targets] == ["w1"]
    assert "tabs" in conn.calls


def test_closing_a_tab_asks_marionette(firefox, monkeypatch):
    """Study mode's whole behaviour rests on this one, and the debug port's close endpoint does
    not exist in Firefox."""
    conn = FakeConn()
    through(monkeypatch, conn)
    assert asyncio.run(browser.close_tab({"id": "w1"})) is True
    assert "close:w1" in conn.calls


def test_a_failed_click_comes_back_in_the_shape_the_caller_expects(firefox, monkeypatch):
    """ok / error / choices, the same either way, so nothing above reads two formats."""
    from jarvis.integrations import firefox_page

    monkeypatch.setattr(firefox_page, "click_text",
                        lambda _c, _p: {"ok": False, "why": "nothing says that",
                                        "choices": ["a", "b"]})
    through(monkeypatch, FakeConn())
    got = asyncio.run(browser.click_text("whatever"))
    assert got["ok"] is False
    assert got["error"] == "nothing says that"
    assert got["choices"] == ["a", "b"]


def test_chromium_never_takes_the_marionette_path(chromium, monkeypatch):
    """The failure this guards is silent: a CDP call against Firefox just times out."""
    conn = FakeConn()
    through(monkeypatch, conn)
    assert browser._firefox_now() is False
    with pytest.raises(Exception):
        # No CDP available in a test, so this must fail rather than quietly succeed through
        # Marionette — which is exactly what it would do if the branch were wrong.
        asyncio.run(browser._targets())
    assert conn.calls == []
