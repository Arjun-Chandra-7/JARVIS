"""What study mode closes, and — more importantly — what it refuses to close.

The asymmetry is the design. Shutting a lecture somebody is midway through is a much worse
failure than leaving one distraction open, because the first one gets study mode turned off for
good. Several tests below exist only to pin that bias in place.

Verified against a real browser, not only against these strings
---------------------------------------------------------------
Driving an isolated Zen: a Shorts tab and an ordinary tab were opened, the verdicts were taken
from the URLs the browser actually reported, and the Short's tab was gone afterwards while the
other one was still there.

That run also turned up the thing worth writing down. YouTube redirects /shorts/<id> to
/watch?v=<id> when the id is not a Short — so the first attempt never reached the Shorts branch
at all, and only looked like it worked because the title was judged unrelated to studying. A
genuine Short does stay on /shorts/, confirmed by opening the Shorts feed and reading where it
landed. Which means the rule below is sound in both directions: a real Short is caught by the
URL before anything can argue about its title, and a /shorts/ link to a normal video becomes an
ordinary /watch URL and is judged on what it is.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.modes import study, watching


# --------------------------------------------------------------------------- shorts
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/shorts/abc123",
    "https://youtube.com/shorts/abc123?feature=share",
    "https://m.youtube.com/shorts/xyz",
])
def test_shorts_are_recognised_however_they_are_linked(url):
    assert watching.is_short(url)


def test_a_short_closes_even_when_its_title_sounds_educational():
    """The whole point of blocking Shorts. The format is the distraction, not the subject — an
    endless feed with no stopping point is what study mode exists to interrupt."""
    verdict = watching.verdict_for("https://www.youtube.com/shorts/a",
                                   "Physics in 60 seconds - YouTube")
    assert verdict.close
    assert "Shorts" in verdict.reason


def test_a_normal_video_is_not_a_short():
    assert not watching.is_short("https://www.youtube.com/watch?v=abc123")


# --------------------------------------------------------------------------- the always-closed
@pytest.mark.parametrize("url,title", [
    ("https://www.instagram.com/reels/", "Instagram"),
    ("https://www.netflix.com/watch/80100172", "Friends"),
    ("https://www.twitch.tv/somebody", "somebody - Twitch"),
])
def test_the_sites_nobody_revises_on_close_on_sight(url, title):
    assert watching.verdict_for(url, title).close


def test_an_ordinary_site_is_left_completely_alone():
    """Study mode is not a firewall. It closes watching sites, not the web."""
    assert not watching.verdict_for("https://github.com/x/y", "some repo").close
    assert not watching.verdict_for("https://en.wikipedia.org/wiki/Optics", "Optics").close


# --------------------------------------------------------------------------- judging a title
@pytest.mark.parametrize("title", [
    "Light Reflection and Refraction Class 10 One Shot",
    "NCERT Chapter 3 Solutions Explained",
    "Trigonometry derivation tutorial",
    "CBSE 2026 sample paper walkthrough",
])
def test_lectures_stay_open(title):
    assert not watching.verdict_for("https://www.youtube.com/watch?v=a", title).close


@pytest.mark.parametrize("title", [
    "my day in delhi vlog",
    "try not to laugh compilation",
    "IPL 2026 highlights",
    "official music video",
])
def test_entertainment_closes(title):
    assert watching.verdict_for("https://www.youtube.com/watch?v=a", title).close


def test_a_title_with_both_kinds_goes_with_whichever_says_more():
    """"Trigonometry meme compilation" is two distraction words against one study word."""
    assert watching.verdict_for("https://www.youtube.com/watch?v=a",
                                "Trigonometry meme compilation").close


def test_an_unclear_title_is_left_open_and_marked_uncertain():
    """The bias, in one test: when nothing is confident, nothing is closed."""
    verdict = watching.verdict_for("https://www.youtube.com/watch?v=a", "(3) Untitled")
    assert not verdict.close
    assert not verdict.certain


def test_the_youtube_suffix_and_unread_count_are_not_part_of_the_title():
    assert watching._clean_title("(12) Algebra basics - YouTube") == "Algebra basics"


# --------------------------------------------------------------------------- remembering
def test_the_video_id_is_found_in_every_link_shape():
    assert watching.video_id("https://www.youtube.com/watch?v=abc") == "abc"
    assert watching.video_id("https://youtu.be/abc") == "abc"
    assert watching.video_id("https://www.youtube.com/shorts/abc") == "abc"


def test_a_judgement_is_remembered_so_the_brain_is_asked_once(monkeypatch):
    """Without this the sweep asks about the same lecture every eight seconds for an hour."""
    study.forget_judgements()
    asked = []

    async def fake_brain(title):
        asked.append(title)
        return True

    monkeypatch.setattr(study, "_ask_the_brain", fake_brain)

    async def run():
        url = "https://www.youtube.com/watch?v=zz"
        for _ in range(4):
            await study._should_close(url, "(3) Untitled")

    asyncio.run(run())
    assert len(asked) == 1, f"asked {len(asked)} times for one video"
    study.forget_judgements()


def test_an_unreachable_brain_leaves_the_tab_open(monkeypatch):
    """Same bias as everywhere else: no answer means no closing."""
    study.forget_judgements()

    async def no_answer(_title):
        return None

    monkeypatch.setattr(study, "_ask_the_brain", no_answer)

    async def run():
        return await study._should_close("https://www.youtube.com/watch?v=q", "(3) Untitled")

    close, why = asyncio.run(run())
    assert close is False
    assert "could not tell" in why
    study.forget_judgements()


def test_the_brain_is_never_asked_about_a_short(monkeypatch):
    """Shorts are settled before any title is read, so there is nothing to ask about."""
    study.forget_judgements()
    asked = []

    async def fake_brain(title):
        asked.append(title)
        return True

    monkeypatch.setattr(study, "_ask_the_brain", fake_brain)

    async def run():
        return await study._should_close("https://www.youtube.com/shorts/s", "Physics explained")

    close, _why = asyncio.run(run())
    assert close is True
    assert asked == []
    study.forget_judgements()
