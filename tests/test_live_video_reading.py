"""Second live round: "it's not able to read the videos on my screen or summarise them".

From the journal and the browser:
* "Tell me what is happening in the video" and "what video am I looking at and give me a
  summary?" matched no video pattern and went to the model ("I can't see videos").
* When one did match, the page read was the tab a new Marionette session lands on — a ChatGPT
  tab — while the lecture was in another tab of the same Zen window.
* Once on the right tab, the transcript was empty: YouTube answers a reused caption URL with an
  empty 200, and the transcript panel waits on timers a background tab barely runs.
* "Yeah, message Papa" reached the model, which composed its own text and sent it to a known
  contact with no approval, then said "I've sent the message".
"""
import asyncio

import pytest

from jarvis import llm, message_command, video_command as vc
from jarvis.screen import page as pg
from jarvis.screen import youtube as yt


# --------------------------------------------------------------------------- what was said

@pytest.mark.parametrize("said", [
    "Tell me what is happening in the video.",
    "Like what video am I looking at and give me a summary?",
    "the video on my screen",
    "summarise this video",
    "what is this video about?",
    "ye video kis baare mein hai",
    "is video mein kya ho raha hai",
    "वीडियो में क्या हो रहा है?",
    "इस video का summary दो",
])
def test_questions_about_the_whole_video(said):
    assert vc.intent(said) == "overview"


def test_whole_video_questions_never_reach_memory():
    assert vc.is_current_context("Tell me what is happening in the video.")
    assert vc.intent("what did he say in yesterday's video") is None


# --------------------------------------------------------------------------- finding the tab

class FakeConn:
    """A Marionette session over tabs {handle: state}; handles are renamed per session."""
    sessions = 0

    def __init__(self, tabs):
        FakeConn.sessions += 1
        self.prefix = f"s{FakeConn.sessions}-"
        self.tabs = tabs
        self.current = self.prefix + next(iter(tabs))
        self.focus_changes = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def _send(self, cmd, params=None):
        if cmd == "WebDriver:GetWindowHandles":
            return [self.prefix + h for h in self.tabs]
        if cmd == "WebDriver:GetWindowHandle":
            return self.current
        if cmd == "WebDriver:SwitchToWindow":
            if not params["handle"].startswith(self.prefix):
                raise RuntimeError("Unable to locate window")
            self.focus_changes.append(params.get("focus", True))
            self.current = params["handle"]
            return None
        raise AssertionError(cmd)

    def _state(self):
        return self.tabs[self.current[len(self.prefix):]]

    def url(self):
        return self._state()["url"]

    def script(self, source, args=None):
        return dict(self._state())


TABS = {
    "chat": {"url": "https://chatgpt.com/c/x", "visible": False, "playing": False, "at": 0},
    "old": {"url": "https://www.youtube.com/watch?v=OLD", "visible": False, "playing": False, "at": 0},
    "lecture": {"url": "https://www.youtube.com/watch?v=LECTURE&t=80s", "visible": False, "playing": True, "at": 80},
}


@pytest.fixture
def zen(monkeypatch):
    conns = []

    def connect(*a, **k):
        conns.append(FakeConn(TABS))
        return conns[-1]

    from jarvis.integrations import marionette
    monkeypatch.setattr(marionette, "reachable", lambda: True)
    monkeypatch.setattr(marionette, "Connection", connect)
    return conns


def test_the_playing_lecture_is_chosen_over_the_tab_the_session_lands_on(zen):
    page = pg.find_video_page()
    assert isinstance(page, pg.MarionettePage) and "LECTURE" in page.url
    assert all(focus is False for c in zen for focus in c.focus_changes)   # nothing brought forward


def test_the_tab_is_found_again_in_a_new_session(zen):
    page = pg.find_video_page()
    got = asyncio.run(page.run("return 1"))           # a fresh session: old handles are gone
    assert got["url"].startswith("https://www.youtube.com/watch?v=LECTURE")


def test_a_moving_timestamp_is_still_the_same_tab():
    assert pg._same_video("https://www.youtube.com/watch?v=A&t=80s", "https://www.youtube.com/watch?v=A&t=95s")
    assert not pg._same_video("https://www.youtube.com/watch?v=A", "https://www.youtube.com/watch?v=B")


def test_rank_prefers_visible_then_playing_then_started():
    assert pg._rank({"visible": True}) > pg._rank({"playing": True, "at": 50})
    assert pg._rank({"playing": True}) > pg._rank({"at": 50})
    assert pg._rank({"at": 50}) > pg._rank({"at": 0})


# --------------------------------------------------------------------------- the transcript

class EmptyCaptionsPage:
    """The live failure: caption reuse returns empty, the panel never fills."""
    async def run(self, body, args=None, timeout=20.0):
        if body is yt._FETCH_TRACK:
            return {"ok": False, "reason": "empty"}
        if body is yt._PANEL:
            raise TimeoutError("background tab")
        raise AssertionError("unexpected script")


def _state(video_id="LECTURE1234"):
    return yt.PlayerState(youtube=True, video_id=video_id, time=80.0,
                          tracks=[{"url": "https://yt/timedtext?x", "lang": "en", "kind": "asr"}])


def test_captions_come_from_yt_dlp_when_the_page_cannot_give_them(monkeypatch):
    calls = []

    def fetch(video_id, lang="en"):
        calls.append((video_id, lang))
        return [yt.Segment(60.0, 5.0, "attention is all you need")], "captions (en, fetched with yt-dlp)"

    monkeypatch.setattr(yt, "ytdlp_transcript", fetch)
    segs, source = asyncio.run(yt.YouTube(EmptyCaptionsPage()).transcript(_state()))
    assert segs[0].text == "attention is all you need" and "yt-dlp" in source
    # Remembered: a second question about the same video does not fetch again.
    asyncio.run(yt.YouTube(EmptyCaptionsPage()).transcript(_state()))
    assert calls == [("LECTURE1234", "en")]


def test_no_source_at_all_is_an_empty_transcript_not_a_crash():
    segs, source = asyncio.run(yt.YouTube(EmptyCaptionsPage()).transcript(_state("NOCAPTIONS12")))
    assert segs == [] and source == ""


def test_json3_is_read_like_the_page_reads_it():
    raw = {"events": [{"tStartMs": 1500, "dDurationMs": 2000, "segs": [{"utf8": "hello "}, {"utf8": "guys"}]},
                      {"tStartMs": 4000}, {"tStartMs": 5000, "segs": [{"utf8": "\n"}]}]}
    assert [(s.start, s.dur, s.text) for s in yt.parse_json3(raw)] == [(1.5, 2.0, "hello guys")]


def test_a_video_id_that_is_not_one_never_reaches_the_command_line():
    assert yt._VIDEO_ID.match("--exec=rm -rf ~") is None
    assert yt._VIDEO_ID.match("bCz4OMemCcA")


# --------------------------------------------------------------------------- the model

def test_a_long_stretch_is_thinned_to_fit_but_keeps_now():
    excerpt = [yt.Segment(float(i), 1.0, "word " * 20) for i in range(600)]
    fitted = vc._fit(excerpt)
    assert sum(len(s.text) for s in fitted) <= vc._EXCERPT_CHARS
    assert fitted[0] is excerpt[0] and fitted[-1] is excerpt[-1]
    short = excerpt[:5]
    assert vc._fit(short) == short


def test_a_short_rate_limit_is_waited_out_once(monkeypatch):
    from jarvis import providers as pv
    provider = pv.Provider("groq", "https://x", "k", "m", "strong")
    monkeypatch.setattr(llm, "candidates", lambda *a, **k: [provider])
    monkeypatch.setattr(pv, "blocked", lambda p: None)
    monkeypatch.setattr(pv, "record_failure", lambda *a, **k: 0)
    monkeypatch.setattr(pv, "record_success", lambda *a, **k: None)
    monkeypatch.setattr(pv, "classify", lambda exc: pv.Failure("rate_limited", "429", retry_after=0.01))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    tries = []

    def ask(*a):
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("429")
        return "an explanation"

    done = llm.complete_sync("s", "p", object(), strength="strong", ask=ask)
    assert done.ok and done.text == "an explanation" and len(tries) == 2


# --------------------------------------------------------------------------- messages

@pytest.mark.parametrize("said,who,body", [
    ("Message Papa Hi.", "Papa", "Hi"),
    ("Message, Papa.", "Papa", ""),
    ("Yeah, message Papa.", "Papa", ""),
])
def test_the_logged_message_phrasings(said, who, body):
    req = message_command.read(said)
    assert (req.who, req.body) == (who, body)


def test_the_model_can_never_send_a_message_unseen(monkeypatch):
    from jarvis.agent.groq_tools import build_registry
    from jarvis.config import Config
    from jarvis.integrations import whatsapp
    seen = []
    monkeypatch.setattr(whatsapp, "smart_send",
                        lambda to, message, **kw: seen.append(kw) or {"message": "Ready to send", "status": "needs_approval"})

    async def compose(*a, **k):
        return "Hi Papa, how are you doing?"

    import jarvis.agent.groq_tools as tools
    monkeypatch.setattr(tools, "_compose_message", compose, raising=False)
    _, dispatch = build_registry(Config(), job_runner=None, confirm_fn=None)
    asyncio.run(dispatch("whatsapp_send", {"to": "Papa", "message": "hi"}))
    asyncio.run(dispatch("message_person", {"name": "Papa", "about": "ask how he is"}))
    assert seen == [{"confirm": True}, {"confirm": True}]
