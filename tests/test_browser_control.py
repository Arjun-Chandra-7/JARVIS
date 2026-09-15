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
    asked = {}

    def fake_launch(url="", wait_s=12.0, restore=False):
        asked["restore"] = restore
        return True

    monkeypatch.setattr(browser, "launch", fake_launch)
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)
    state = browser.ensure(allow_restart=True)
    assert state["ok"] and state["state"] == "launched"
    # Regaining control costs a restart; a restart should not also cost every open tab.
    assert asked["restore"] is True


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


# --------------------------------------------------- saying what a search actually found
def test_the_thing_asked_for_is_named_when_it_is_there():
    from jarvis.integrations.browser import describe_results

    got = describe_results("friends", ["Friends", "Friends with Benefits", "Suits"])
    assert "Friends" in got and "first result" in got


def test_its_position_is_given_when_it_is_not_first():
    from jarvis.integrations.browser import describe_results

    assert "result 3" in describe_results("suits", ["Friends", "Gilmore Girls", "Suits"])


def test_a_miss_is_reported_as_a_miss():
    """Listing whatever happened to be on screen let a failed search read like a success."""
    from jarvis.integrations.browser import describe_results

    got = describe_results("friends", ["Home", "Shows", "Movies"])
    assert got.startswith("I don't see friends")


def test_nothing_found_says_nothing():
    from jarvis.integrations.browser import describe_results

    assert describe_results("friends", []) == ""


# ------------------------------------------------------------------ drawing on a canvas
def test_a_stroke_needs_at_least_two_points():
    """A canvas is the one thing on a page with no model inside it, so strokes are all there is;
    a single point is a click, not a line."""
    import asyncio

    from jarvis.integrations import browser

    got = asyncio.run(browser.draw_path([[(10, 10)]]))
    assert got["ok"] is False


def test_nothing_to_draw_is_refused_rather_than_faked():
    import asyncio

    from jarvis.integrations import browser

    assert asyncio.run(browser.draw_path([]))["ok"] is False
    assert asyncio.run(browser.draw_path(None))["ok"] is False


def test_each_stroke_is_replayed_once(monkeypatch):
    """One call per stroke, not one per point: CDP charges a round trip for every event, and
    sending them without waiting let the renderer coalesce moves into broken dashes."""
    import asyncio

    from jarvis.integrations import browser

    scripts = []

    class FakeSession:
        async def js(self, expression, timeout=20.0):
            scripts.append(expression)
            return {"ok": True, "points": expression.count("[")}

    async def fake_with_page(fn, timeout=25.0, **kw):
        return await fn(FakeSession())

    monkeypatch.setattr(browser, "_with_page", fake_with_page)
    got = asyncio.run(browser.draw_path([[(0, 0), (5, 5)], [(9, 9), (1, 1)]]))
    assert got["strokes"] == 2
    assert len(scripts) == 2
    # Each replay presses once and releases once, so strokes cannot merge into one scribble.
    for script in scripts:
        assert script.count('fire("down"') == 1
        assert script.count('fire("up"') == 1


def test_the_drawable_area_is_the_clear_part_not_the_whole_stage(monkeypatch):
    """A whiteboard floats its toolbar and panels over the board. A stroke starting under one is
    delivered to the panel, not the board, and vanishes — which is why two thirds of a drawing
    went missing on a real site while a bare canvas looked perfect."""
    import asyncio

    from jarvis.integrations import browser

    async def fake_with_page(fn, timeout=25.0, **kw):
        class S:
            async def js(self, expression, timeout=20.0):
                # what the real probe returned on onlinewhiteboard.org
                return {"x": 150, "y": 103, "w": 449, "h": 828, "tag": "svg", "covered": 43}
        return await fn(S())

    monkeypatch.setattr(browser, "_with_page", fake_with_page)
    box = asyncio.run(browser.canvas_box())
    assert box["w"] < 898              # narrower than the stage: the panels are excluded
    assert box["covered"] == 43


def test_a_surface_that_is_mostly_covered_is_still_refused(monkeypatch):
    import asyncio

    from jarvis.integrations import browser

    async def fake_with_page(fn, timeout=25.0, **kw):
        class S:
            async def js(self, expression, timeout=20.0):
                return None            # nothing clear enough to draw on
        return await fn(S())

    monkeypatch.setattr(browser, "_with_page", fake_with_page)
    assert asyncio.run(browser.canvas_box()) is None
