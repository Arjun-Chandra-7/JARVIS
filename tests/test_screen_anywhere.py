"""The same questions anywhere: any web page, any site's video, any desktop app.

"Explain this", "summarise this", "what did he just say", "ye kya hai" were answered for YouTube
only. Here the thing in front of the person is found (the focused window, the tab matching it)
and read from itself: a page's selection, visible text or article; a video's own caption track;
an app's accessibility text or the screen's OCR. Nothing is guessed when nothing can be read.
"""
import asyncio

import pytest

from jarvis import route_log, screen_context as sc, video_command as vc
from jarvis.screen import youtube as yt
from jarvis.vision.ocr import Word


class AnyPage:
    """A page on any site. ``video`` is None or {time, duration, paused, captions}."""
    kind = "fake"

    def __init__(self, *, selection="", in_view="", body="", video=None, title="Page"):
        self.data = {"url": "https://example.org/x", "title": title, "selection": selection,
                     "in_view": in_view, "body": body, "video": video}
        self.paused = False

    async def run(self, body, args=None, timeout=20.0):
        if body is yt._STATE:
            return {"youtube": False, "url": self.data["url"], "title": self.data["title"]}
        if body is sc._PAGE:
            return dict(self.data)
        if body is yt._CONTROL:
            self.paused = True
            return {"ok": True, "paused": True, "time": (self.data["video"] or {}).get("time", 0)}
        raise AssertionError("unexpected script")


@pytest.fixture
def wired(monkeypatch):
    from jarvis.llm import Completion
    seen = {"prompts": []}

    async def fake_complete(system, prompt, config=None, temperature=0.2, timeout=60.0, strength="default"):
        seen["prompts"].append(prompt)
        seen["system"] = system
        return Completion(text="Here is what that means.", provider="groq:test", quality="strong")

    monkeypatch.setattr("jarvis.llm.complete_detailed", fake_complete)
    route_log.RECENT.clear()

    def use(page=None, window=("", ""), app_text=("", "")):
        monkeypatch.setattr("jarvis.screen.page.active_page", lambda: page)
        monkeypatch.setattr(sc, "active_window", lambda: window)
        monkeypatch.setattr(sc, "read_app", lambda *_a, **_k: app_text)

    seen["use"] = use
    return seen


def ask(text):
    return asyncio.run(vc.handle(text, None))


def last():
    return [r for r in route_log.RECENT if str(r.get("intent", "")).startswith("screen.")][-1]


# --------------------------------------------------------------------------- what counts

@pytest.mark.parametrize("said", [
    "Explain this", "summarise this page", "what am I looking at?", "what is this article about",
    "explain this error", "help me understand this", "give me a summary of this article",
    "ye kya hai", "isko samjhao", "iska summary do", "is code ko samjhao",
    "इसे समझाओ", "ये क्या है?", "इस page को समझाओ", "इसका मतलब क्या है",
])
def test_questions_about_whatever_is_in_front(said):
    assert vc.intent(said) == "page"


@pytest.mark.parametrize("said", [
    "what is this theorem called", "explain quantum physics", "what is the pythagoras theorem",
    "tell me a joke", "open youtube", "what did we discuss yesterday",
])
def test_ordinary_questions_are_left_alone(said):
    assert vc.intent(said) is None


# --------------------------------------------------------------------------- web pages

def test_explain_this_reads_what_is_in_view(wired):
    wired["use"](AnyPage(in_view="Photosynthesis converts light energy into chemical energy",
                         body="whole article"))
    assert ask("explain this") == "Here is what that means."
    p = wired["prompts"][-1]
    assert "Photosynthesis converts light" in p and "whole article" not in p
    assert last()["context"] == "page_view" and last()["memory"] == "not_consulted"


def test_a_selection_is_what_this_means(wired):
    wired["use"](AnyPage(selection="E = mc squared", in_view="lots of other text"))
    ask("isko samjhao")
    p = wired["prompts"][-1]
    assert "E = mc squared" in p and "lots of other text" not in p
    assert "Hinglish (Roman script)" in p and last()["context"] == "page_selection"


def test_summarise_reads_the_whole_article(wired):
    wired["use"](AnyPage(in_view="the first screen", body="the first screen and everything after it"))
    ask("summarise this page")
    assert "everything after it" in wired["prompts"][-1] and last()["context"] == "page_text"


def test_a_long_page_is_clipped_for_the_model(wired):
    wired["use"](AnyPage(body="word " * 10000))
    ask("summarise this article")
    assert len(wired["prompts"][-1]) < vc._EXCERPT_CHARS + 2000


def test_an_empty_page_is_not_guessed_at(wired):
    wired["use"](AnyPage())
    assert "can't read anything" in ask("explain this")
    assert wired["prompts"] == []


# --------------------------------------------------------------------------- any site's video

CAPTIONS = [[30.0, "cells need energy"], [55.0, "the mitochondria makes ATP"], [300.0, "later topic"]]


def test_a_video_on_any_site_is_explained_from_its_own_captions(wired):
    wired["use"](AnyPage(video={"time": 60.0, "duration": 900.0, "paused": False, "captions": CAPTIONS}))
    ask("what did he just say?")
    p = wired["prompts"][-1]
    assert "the mitochondria makes ATP" in p and "later topic" not in p and "Video position: 1:00" in p
    assert last()["context"] == "page_captions"


def test_pause_and_explain_on_any_site(wired):
    page = AnyPage(video={"time": 60.0, "duration": 900.0, "paused": False, "captions": CAPTIONS})
    wired["use"](page)
    assert ask("pause and explain this part").startswith("Paused. ") and page.paused


def test_a_video_without_captions_is_said_so(wired):
    wired["use"](AnyPage(video={"time": 60.0, "duration": 900.0, "paused": False, "captions": []},
                         in_view="Lecture 4: Cell biology"))
    assert "no captions" in ask("what did he just say?") and wired["prompts"] == []
    # "What is this video?" can still be answered from the page around it.
    ask("what is this video about")
    assert "Lecture 4: Cell biology" in wired["prompts"][-1]


def test_asking_about_a_video_on_a_page_without_one(wired):
    wired["use"](AnyPage(in_view="just an article"))
    assert "no video on this page" in ask("what did he just say?")


# --------------------------------------------------------------------------- desktop apps

def test_a_desktop_app_is_read_through_accessibility(wired):
    text = "Chapter 3 notes. " * 20
    wired["use"](None, window=("gnome-text-editor", "notes.txt - Text Editor"), app_text=(text, ""))
    ask("explain this")
    p = wired["prompts"][-1]
    assert "Window: notes.txt - Text Editor" in p and "Chapter 3 notes" in p
    assert last()["context"] == "app_atspi"


def test_a_desktop_app_without_accessibility_is_read_by_ocr(wired):
    wired["use"](None, window=("code", "main.py - Visual Studio Code"), app_text=("", "def add(a, b):\n  return a + b"))
    ask("ye kya hai")
    p = wired["prompts"][-1]
    assert "return a + b" in p and "OCR" in p and last()["context"] == "app_ocr"


def test_no_browser_at_all_still_reads_the_screen(wired):
    wired["use"](None, app_text=("", "Error: file not found"))
    ask("explain this error")
    assert "file not found" in wired["prompts"][-1]


def test_nothing_readable_anywhere(wired):
    wired["use"](None)
    reply = ask("explain this")
    assert "restart the browser with control" in reply and wired["prompts"] == []


# --------------------------------------------------------------------------- finding the tab

def test_the_tab_matching_the_focused_window_wins():
    window = "Photosynthesis - Wikipedia — Zen Browser"
    wiki = {"url": "https://en.wikipedia.org/wiki/Photosynthesis", "title": "Photosynthesis - Wikipedia"}
    playing = {"url": "https://www.youtube.com/watch?v=x", "title": "Music", "playing": True, "visible": True}
    assert sc.rank_tab(wiki, window) > sc.rank_tab(playing, window, prefer_video=True)


def test_without_a_window_title_visible_then_playing():
    assert sc.rank_tab({"visible": True}) > sc.rank_tab({"playing": True})
    assert sc.rank_tab({"url": "https://youtube.com/watch?v=a"}, prefer_video=True) > sc.rank_tab({"url": "https://a.b"},
                                                                                                    prefer_video=True)


@pytest.mark.parametrize("app,window,browser", [
    ("", "Photosynthesis - Wikipedia — Zen Browser", True),
    ("", "Inbox - Gmail - Google Chrome", True),
    ("firefox", "Mozilla Firefox", True),
    ("gnome-text-editor", "notes.txt - Text Editor", False),
    ("code", "main.py - Visual Studio Code", False),
])
def test_which_windows_are_browsers(app, window, browser):
    assert sc.is_browser(app, window) is browser


def test_ocr_words_become_lines_in_reading_order():
    words = [Word("world", 60, 10, 50, 5, 90, 15, 0.9), Word("hello", 10, 12, 0, 7, 40, 17, 0.9),
             Word("second", 10, 50, 0, 45, 40, 55, 0.9), Word("noise", 10, 90, 0, 85, 40, 95, 0.2)]
    assert sc._ocr_lines(words) == "hello world\nsecond"


def test_the_video_questions_through_the_router_never_reach_memory(wired, monkeypatch):
    from jarvis import commands
    from jarvis.config import CONFIG
    monkeypatch.setattr("jarvis.power.asleep", lambda: False)
    monkeypatch.setattr("jarvis.memory.search.recall",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("memory consulted")))
    wired["use"](AnyPage(in_view="An essay about climate change"))
    reply = asyncio.run(commands.handle("summarise this page", CONFIG, "voice"))
    assert reply == "Here is what that means." and "climate change" in wired["prompts"][-1]


# --------------------------------------------------------------------------- free-form questions

@pytest.mark.parametrize("said", [
    "On my screen, what is a sequence output in regarding to whatever is on my screen?",
    "So what is a sequential input and a sequential output in this scenario?",
    "So take a screenshot of my screen and explain me what sequential",
    "is video mein ye formula kya hai",
    "इस वीडियो में ये formula क्यों आया?",
])
def test_questions_that_point_at_the_screen(said):
    # Heard live; all went to the local model, which said it could not see the screen.
    assert vc.intent(said) == "ask"


def test_a_screen_question_is_answered_with_labelled_general_knowledge(wired):
    wired["use"](AnyPage(in_view="Figure 3: an RNN unrolled over time steps x1, x2, x3"))
    ask("what is a sequential input in this diagram?")
    p = wired["prompts"][-1]
    assert "RNN unrolled" in p and "general knowledge" in p and "Answer their question" in p
    assert "never guess" in wired["system"] and "concrete example" in wired["system"]
