"""«open YouTube» → «search for X» → «play the first video», through the real command layer.

Found on the end-to-end voice run with the browser not under automation: the search went to the
model and came back as "I didn't catch that", and "play the first video" became a web search for
the words "first video". The browser and yt-dlp are replaced here; the routing is real.
"""
import asyncio
import time

import pytest

from jarvis import commands, context, youtube_command

RESULTS = [("https://www.youtube.com/watch?v=AAAAAAAAAAA", "Pythagoras in five minutes"),
           ("https://www.youtube.com/watch?v=BBBBBBBBBBB", "Proofs of the theorem"),
           ("https://www.youtube.com/watch?v=CCCCCCCCCCC", "Right triangles explained")]


@pytest.fixture
def world(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("jarvis.power.asleep", lambda: False)
    monkeypatch.setattr("jarvis.integrations.apps.open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(youtube_command, "top_results", lambda q, n=5, timeout=12.0: list(RESULTS))
    context.forget("voice")
    youtube_command._found.clear()
    youtube_command._pending.clear()
    config = type("C", (), {"browser_control": False})()
    return opened, config


def say(text, config):
    return asyncio.run(_say(text, config))


async def _say(text, config):
    reply = await commands.handle(text, config, "voice")
    await asyncio.sleep(0)            # let the background result fetch run
    task = youtube_command._pending.get("voice")
    if task is not None:
        await task
    return reply


def test_the_lecture_flow_opens_search_then_the_first_result(world):
    opened, config = world
    context.note_opened("voice", site="YouTube", target="YouTube")      # "open YouTube" worked
    assert say("Search for Pythagoras theorem.", config) == "Searching YouTube for Pythagoras theorem."
    assert opened[-1] == "https://www.youtube.com/results?search_query=Pythagoras+theorem"
    assert say("Play the first video.", config) == "Opening “Pythagoras in five minutes”."
    assert opened[-1] == RESULTS[0][0]


def test_the_second_and_the_last(world):
    opened, config = world
    say("search youtube for pythagoras", config)
    assert say("play the second one", config) == "Opening “Proofs of the theorem”."
    assert say("open the last video", config) == "Opening “Right triangles explained”."


def test_search_without_youtube_open_is_not_taken(world):
    opened, config = world
    reply = asyncio.run(youtube_command.handle("search for pythagoras theorem"))
    assert reply is None and opened == []


def test_play_the_first_video_is_never_a_web_search_for_first_video():
    from jarvis.open_command import parse
    assert parse("play the first video") is None
    assert parse("open the second one") is None
    assert parse("play despacito") == "despacito"


def test_opening_youtube_without_automation_is_still_remembered(monkeypatch):
    from jarvis import open_command
    from jarvis.integrations import apps
    monkeypatch.setattr(apps, "open_url", lambda url: True)
    context.forget("voice")
    context.set_current("voice")
    config = type("C", (), {"browser_control": False})()
    assert asyncio.run(open_command.run("YouTube", config)) == "Opened YouTube."
    assert context.of("voice").site == "YouTube"


def test_the_routing_itself_is_fast(world):
    """Recognised deterministic commands begin well inside 200 ms of the transcript arriving."""
    opened, config = world
    context.note_opened("voice", site="YouTube", target="YouTube")
    t = time.perf_counter()
    asyncio.run(commands.handle("search for pythagoras theorem", config, "voice"))
    assert time.perf_counter() - t < 0.2
