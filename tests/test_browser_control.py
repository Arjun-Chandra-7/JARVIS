"""Precision browser control: the pure decisions, without a browser.

The parts that need Chromium are exercised in the live check described in docs/PRECISION.md; what
is pinned here is every rule that decides *what* to do, because those are the ones that silently
send a request to the wrong place.
"""
import asyncio

import pytest

from jarvis.integrations import browser


# ----------------------------------------------------------------- destinations vs titles
@pytest.mark.parametrize("name,url", [
    ("netflix", "https://www.netflix.com"),
    ("Netflix", "https://www.netflix.com"),
    ("youtube", "https://www.youtube.com"),
    ("hotstar", "https://www.hotstar.com"),
])
def test_known_sites_resolve(name, url):
    assert browser.resolve_site(name) == url


def test_bare_domain_becomes_https():
    assert browser.resolve_site("youtube.com") == "https://youtube.com"


def test_full_url_passes_through():
    assert browser.resolve_site("https://example.com/x?y=1") == "https://example.com/x?y=1"


def test_file_url_is_not_searched_for():
    """A file:// URL matched no scheme rule and was sent to a search engine."""
    assert browser.resolve_site("file:///tmp/a.html") == "file:///tmp/a.html"


def test_free_text_becomes_a_search():
    assert browser.resolve_site("how tall is everest").startswith(
        "https://www.google.com/search?q=")


def test_spelled_out_title_is_not_a_domain():
    """'F.R.I.E.N.D.S' is how speech renders a spelled title, and it looks exactly like a domain."""
    assert not browser._is_known_destination("f.r.i.e.n.d.s")
    assert not browser._is_known_destination("F.R.I.E.N.D.S")
    assert "friends" in browser.resolve_site("f.r.i.e.n.d.s")


def test_spelled_out_title_does_not_become_a_url():
    assert not browser.resolve_site("f.r.i.e.n.d.s").startswith("https://f.r.i")


@pytest.mark.parametrize("name", ["netflix", "youtube.com", "https://x.com", "about:blank"])
def test_destinations_are_recognised(name):
    assert browser._is_known_destination(name)


@pytest.mark.parametrize("name", ["the office", "friends", "breaking bad", "arjun"])
def test_titles_are_not_destinations(name):
    assert not browser._is_known_destination(name)


# ----------------------------------------------------------------- keys
def test_known_keys_map():
    assert browser._KEYS["enter"][0] == "Enter"
    assert browser._KEYS["space"][0] == " "


def test_unknown_key_is_refused():
    res = asyncio.run(browser.press_key("hyperspace"))
    assert not res["ok"] and "don't know" in res["error"]


def test_single_letter_key_is_accepted(monkeypatch):
    """Video sites use bare letters — f for fullscreen, m for mute."""
    sent = []

    async def fake_with_page(fn, timeout=25.0, **kw):
        class S:
            async def call(self, method, params=None, timeout=20.0):
                sent.append(params)
                return {}
        return await fn(S())

    monkeypatch.setattr(browser, "_with_page", fake_with_page)
    res = asyncio.run(browser.press_key("f"))
    assert res["ok"]
    assert any(p.get("key") == "f" for p in sent)


# ----------------------------------------------------------------- readiness reporting
def test_ensure_reports_missing_browser(monkeypatch):
    monkeypatch.setattr(browser, "control_ready", lambda timeout=1.5: False)
    monkeypatch.setattr(browser, "_exe", lambda: None)
    state = browser.ensure()
    assert not state["ok"] and state["state"] == "missing"


def test_ensure_asks_before_restarting_a_running_browser(monkeypatch):
    """Restarting loses the user's tabs, so it must never happen without being asked."""
    monkeypatch.setattr(browser, "control_ready", lambda timeout=1.5: False)
    monkeypatch.setattr(browser, "_exe", lambda: "/usr/bin/opera-gx")
    monkeypatch.setattr(browser, "is_running", lambda: True)
    stopped = []
    monkeypatch.setattr(browser, "stop", lambda: stopped.append(1))

    state = browser.ensure(allow_restart=False)
    assert state["state"] == "needs_restart"
    assert not stopped, "it restarted the browser without being allowed to"


def test_ensure_restarts_when_allowed(monkeypatch):
    monkeypatch.setattr(browser, "control_ready", lambda timeout=1.5: False)
    monkeypatch.setattr(browser, "_exe", lambda: "/usr/bin/opera-gx")
    monkeypatch.setattr(browser, "is_running", lambda: True)
    monkeypatch.setattr(browser, "stop", lambda: None)
    monkeypatch.setattr(browser, "launch", lambda url="", wait_s=12.0: True)
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)
    state = browser.ensure(allow_restart=True)
    assert state["ok"] and state["state"] == "launched"


def test_profile_is_the_users_own(monkeypatch):
    """A scratch profile would have no Netflix session, so 'select my profile' could not work."""
    monkeypatch.setattr(browser, "PROFILE_DIR", "")
    assert browser._profile_dir().endswith(".config/opera-gx")


# ------------------------------------------- a mis-heard site name is still a site
def test_a_misheard_site_is_a_destination_not_a_search():
    """"Open Networks" means go to Netflix, not search Netflix for the word "Networks"."""
    from jarvis.integrations.browser import _is_known_destination

    assert _is_known_destination("Networks")
    assert _is_known_destination("Net Flix")
    assert _is_known_destination("youtube")


def test_real_titles_are_still_searched_for():
    """The fuzzy match must not swallow things that are genuinely titles."""
    from jarvis.integrations.browser import _is_known_destination

    for title in ("friends", "f.r.i.e.n.d.s", "the office", "breaking bad"):
        assert not _is_known_destination(title), title


# ------------------------------------------------- staying on one tab across a conversation
def test_jarvis_keeps_driving_the_tab_it_chose():
    """Re-picking "the first page CDP lists" scatters a sequence across windows."""
    import asyncio

    from jarvis.integrations import browser

    targets = [{"id": "A", "type": "page", "url": "https://www.netflix.com/browse"},
               {"id": "B", "type": "page", "url": "https://example.com"}]

    async def fake_targets():
        return targets

    old, old_focus = browser._targets, browser._focus
    try:
        browser._targets = fake_targets
        browser.focus_on("B")
        assert asyncio.run(browser._active_page())["id"] == "B"
        # Order changes underneath us; the chosen tab still wins.
        targets.reverse()
        assert asyncio.run(browser._active_page())["id"] == "B"
        # When it closes, fall back rather than failing.
        targets[:] = [t for t in targets if t["id"] != "B"]
        assert asyncio.run(browser._active_page())["id"] == "A"
        assert browser._focus is None
    finally:
        browser._targets, browser._focus = old, old_focus


def test_a_closed_tab_does_not_strand_jarvis():
    import asyncio

    from jarvis.integrations import browser

    async def none_at_all():
        return []

    old, old_focus = browser._targets, browser._focus
    try:
        browser._targets = none_at_all
        browser.focus_on("gone")
        assert asyncio.run(browser._active_page()) is None
    finally:
        browser._targets, browser._focus = old, old_focus
