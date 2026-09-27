"""The semantic screen model and the YouTube slice, against fakes shaped like the real sources."""
import asyncio

import pytest

from jarvis import video_command as vc
from jarvis.screen_context import _PAGE as _PAGE_SCRIPT
from jarvis.screen import model as sm
from jarvis.screen import providers as pv
from jarvis.screen import youtube as yt


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- environment

def test_detects_this_kind_of_desktop():
    env = sm.detect_environment({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0",
                                 "DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"})
    assert env.session == "wayland" and env.xwayland
    assert any("0,0" in l for l in env.limitations)


def test_x11_has_no_wayland_caveats():
    env = sm.detect_environment({"DISPLAY": ":0"})
    assert env.session == "x11" and not any("0,0" in l for l in env.limitations)


# --------------------------------------------------------------------------- providers

def test_atspi_nodes_become_elements_and_secrets_stay_secret():
    e = pv.element_from_atspi({"name": "", "role": "password text", "rect": [0, 0, 100, 20],
                               "actions": [], "path": [0, 2, 1], "states": ["editable", "focusable"],
                               "value": "hunter2", "secret": True}, "Settings", "Wi-Fi")
    assert e.secret and e.value == "" and e.id == "atspi:0.2.1" and e.parent == "atspi:0.2"
    assert "set_text" in e.actions and "focus" in e.actions


def test_dom_nodes_become_elements():
    e = pv.element_from_dom({"ref": "7", "role": "entry", "name": "Search", "value": "pythagoras",
                             "rect": [10, 10, 300, 30], "states": ["focused", "enabled", "editable"],
                             "editable": True, "scrollable": False, "secret": False},
                            "https://www.youtube.com/", "YouTube")
    assert e.id == "dom:7" and e.focused and e.value == "pythagoras" and "set_text" in e.actions


class FakeProvider:
    """A little window: a search box, two buttons, a password field. Acts like a real source:
    typing changes the value, pressing Play flips its state — unless told to ignore actions."""

    source = "atspi"

    def __init__(self, inert=False):
        self.inert = inert
        mk = lambda i, role, name, **kw: sm.ScreenElement(id=f"atspi:{i}", role=role, name=name,
                                                          source="atspi", confidence=0.95, **kw)
        self.items = {
            "1": mk(1, "entry", "Search", states=frozenset({"editable", "focusable"})),
            "2": mk(2, "push button", "Play", states=frozenset({"enabled"})),
            "3": mk(3, "push button", "Play next", states=frozenset({"enabled"})),
            "4": mk(4, "password text", "Password", states=frozenset({"editable", "secret"})),
        }

    async def elements(self):
        return list(self.items.values())

    async def act(self, element, action, text=""):
        if self.inert:
            return {"ok": True}
        key = element.id.split(":")[1]
        old = self.items[key]
        if action == "set_text":
            self.items[key] = sm.ScreenElement(**{**old.__dict__, "value": text})
        elif action == "focus":
            self.items[key] = sm.ScreenElement(**{**old.__dict__, "states": old.states | {"focused"}})
        elif action == "invoke":
            self.items[key] = sm.ScreenElement(**{**old.__dict__, "name": "Pause"})
        return {"ok": True}

    async def refresh(self, element):
        return self.items.get(element.id.split(":")[1])


def _model(**kw):
    return sm.ScreenModel([FakeProvider(**kw)], env=sm.detect_environment({"DISPLAY": ":0"}))


def test_find_by_spoken_description():
    m = _model()
    found, _ = _run(m.find("the search field"))
    assert found.name == "Search"
    found, _ = _run(m.find("play button"))
    assert found.name == "Play"


def test_find_refuses_a_coin_toss():
    m = _model()
    m.providers[0].items["5"] = sm.ScreenElement(id="atspi:5", role="push button", name="Play",
                                                 source="atspi", confidence=0.95, bounds=(1, 1, 1, 1))
    m.providers[0].items["5"] = sm.ScreenElement(id="atspi:5", role="link", name="Play",
                                                 source="atspi", confidence=0.95)
    found, close = _run(m.find("play"))
    assert found is None and len(close) >= 2


def test_type_is_verified_by_reading_the_value_back():
    m = _model()
    box, _ = _run(m.find("search"))
    result = _run(m.type(box, "pythagoras theorem"))
    assert result.succeeded and result.after.value == "pythagoras theorem"


def test_a_click_that_changes_nothing_is_not_success():
    m = _model(inert=True)
    play, _ = _run(m.find("play button"))
    result = _run(m.invoke(play))
    assert result.ok and not result.verified and not result.succeeded
    assert "can't see that it took effect" in result.message


def test_focus_is_verified():
    m = _model()
    box, _ = _run(m.find("search"))
    assert _run(m.focus(box)).succeeded


def test_never_types_into_a_password_field():
    m = _model()
    pw, _ = _run(m.find("password"))
    result = _run(m.type(pw, "x"))
    assert not result.ok and "password" in result.message
    assert m.read(pw) == ""


# --------------------------------------------------------------------------- the YouTube page

TRANSCRIPT = [
    {"start": 60.0, "dur": 5.0, "text": "take a right triangle with sides a and b"},
    {"start": 65.0, "dur": 6.0, "text": "the side opposite the right angle is c, the hypotenuse"},
    {"start": 71.0, "dur": 6.0, "text": "then a squared plus b squared equals c squared"},
    {"start": 77.0, "dur": 5.0, "text": "because the four triangles fill the same area"},
    {"start": 200.0, "dur": 5.0, "text": "now an example with three four five"},
]


class FakePage:
    kind = "fake"

    def __init__(self, *, youtube=True, time=80.0, paused=False, tracks=True, track_ok=True,
                 panel=False, stuck=False):
        self.youtube, self.time, self.paused = youtube, time, paused
        self.tracks, self.track_ok, self.panel, self.stuck = tracks, track_ok, panel, stuck
        self.calls = []

    async def run(self, body, args=None, timeout=20.0):
        self.calls.append(body)
        if body is _PAGE_SCRIPT:
            return {"url": "https://example.com", "title": "Example", "selection": "",
                    "in_view": "Example page text", "body": "Example page text", "video": None}
        if body is yt._STATE:
            if not self.youtube:
                return {"youtube": False, "url": "https://example.com", "title": "Example"}
            return {"youtube": True, "url": "https://www.youtube.com/watch?v=abc", "videoId": "abc",
                    "fresh": True, "title": "Pythagoras Theorem | Class 10", "channel": "Maths Wala",
                    "time": self.time, "duration": 600.0, "paused": self.paused, "chapter": "Proof",
                    "liveCaption": "then a squared plus b squared", "ad": False,
                    "playerError": getattr(self, "error", ""),
                    "tracks": [{"url": "https://yt/timedtext?x", "lang": "en", "kind": "asr", "name": "English (auto)"},
                               {"url": "https://yt/timedtext?y", "lang": "hi", "kind": "", "name": "Hindi"}]
                    if self.tracks else []}
        if body is yt._FETCH_TRACK:
            return {"ok": True, "segments": TRANSCRIPT} if self.track_ok else {"ok": False, "reason": "empty"}
        if body is yt._PANEL:
            if not self.panel:
                return {"ok": False, "reason": "no transcript button"}
            return {"ok": True, "rows": [{"stamp": "1:00", "text": TRANSCRIPT[0]["text"]},
                                         {"stamp": "1:11", "text": TRANSCRIPT[2]["text"]}]}
        if body is yt._CONTROL:
            action = args[0]
            if not self.stuck:
                self.paused = action == "pause"
            return {"ok": True, "paused": self.paused, "time": self.time,
                    "advanced": not self.paused and not getattr(self, "frozen", False)}
        raise AssertionError("unexpected script")


def test_state_is_read_from_the_player():
    s = _run(yt.YouTube(FakePage()).state())
    assert s.youtube and s.time == 80.0 and s.paused is False and s.chapter == "Proof"
    assert s.title.startswith("Pythagoras")


def test_human_captions_beat_auto_generated():
    tracks = [{"lang": "en", "kind": "asr"}, {"lang": "hi", "kind": ""}]
    assert yt.pick_track(tracks)["lang"] == "hi"
    assert yt.pick_track([{"lang": "en", "kind": "asr"}])["lang"] == "en"
    assert yt.pick_track([]) is None


def test_transcript_from_track_then_panel():
    page = FakePage(track_ok=False, panel=True)
    player = yt.YouTube(page)
    segs, source = _run(player.transcript(_run(player.state())))
    assert source == "the transcript panel" and segs[1].start == 71.0
    segs, source = _run(yt.YouTube(FakePage()).transcript(_run(yt.YouTube(FakePage()).state())))
    assert "captions" in source and len(segs) == 5


def test_window_and_clock():
    segs = [yt.Segment(**s) for s in TRANSCRIPT]
    assert [s.start for s in yt.window(segs, 80, before=15, after=0)] == [65.0, 71.0, 77.0]
    assert yt.clock(71) == "1:11" and yt.clock(3725) == "1:02:05"
    assert yt.parse_stamp("1:02:05") == 3725.0


def test_pause_is_verified_on_the_player():
    page = FakePage()
    out = _run(yt.YouTube(page).control("pause"))
    assert out["verified"] and page.paused
    stuck = _run(yt.YouTube(FakePage(stuck=True)).control("pause"))
    assert stuck["ok"] and not stuck["verified"]


# --------------------------------------------------------------------------- the spoken requests

@pytest.mark.parametrize("said,kind", [
    ("Explain what he just said", "just_said"),
    ("what did the teacher just say?", "just_said"),
    ("Why is this step valid?", "just_said"),
    ("abhi kya bola, samjhao", "just_said"),
    ("Explain what is currently on screen", "on_screen"),
    ("explain this equation", "on_screen"),
    ("screen pe kya hai", "on_screen"),
    ("Pause and explain this part", "pause_explain"),
    ("ruko aur samjhao", "pause_explain"),
    ("Summarise the last two minutes", "summary"),
    ("pichle do minute ka summary do", "summary"),
    ("open youtube", None),
    ("what is the pythagoras theorem", None),
])
def test_intents(said, kind):
    assert vc.intent(said) == kind


def test_language_follows_the_question():
    assert vc.language_of("abhi kya bola, samjhao") == "Hinglish (Roman script)"
    assert vc.language_of("यह क्या है") == "Hindi"
    assert vc.language_of("Explain what he just said") == "English"


@pytest.fixture
def wired(monkeypatch):
    seen = {}

    from jarvis.llm import Completion

    async def fake_complete(system, prompt, config=None, temperature=0.2, timeout=60.0, strength="default"):
        seen["system"], seen["prompt"], seen["strength"] = system, prompt, strength
        return Completion(text="Because the squares on the two shorter sides add up to the square on "
                               "the hypotenuse.", provider="groq:test", quality="strong")

    monkeypatch.setattr("jarvis.llm.complete_detailed", fake_complete)

    def use(page):
        monkeypatch.setattr("jarvis.screen.page.active_page", lambda: page)
        return page
    seen["use"] = use
    return seen


def test_explain_what_he_just_said_uses_the_transcript_around_now(wired):
    wired["use"](FakePage(time=80.0))
    reply = _run(vc.handle("Explain what he just said", None))
    assert reply.startswith("Because the squares")
    p = wired["prompt"]
    assert "a squared plus b squared equals c squared" in p and "now an example" not in p
    assert "Playback position: 1:20" in p and "Answer in English." in p
    assert "Class 10" in wired["system"]


def test_pause_and_explain_pauses_first_and_checks(wired):
    page = wired["use"](FakePage(time=80.0))
    reply = _run(vc.handle("Pause and explain this part", None))
    assert page.paused and reply.startswith("Paused. ")


def test_pause_that_does_not_take_is_reported(wired):
    wired["use"](FakePage(stuck=True))
    reply = _run(vc.handle("pause and explain this part", None))
    assert reply.startswith("I couldn't pause the video")
    assert "prompt" not in wired


def test_summary_covers_the_last_two_minutes(wired):
    wired["use"](FakePage(time=205.0))
    _run(vc.handle("Summarise the last two minutes", None))
    p = wired["prompt"]
    assert "now an example" in p and "take a right triangle" not in p
    assert "Transcript from 1:25 to 3:25" in p


def test_hinglish_question_gets_a_hinglish_answer(wired):
    wired["use"](FakePage())
    _run(vc.handle("abhi kya bola, samjhao", None))
    assert "Answer in Hinglish" in wired["prompt"]


def test_no_transcript_says_so_to_the_model(wired):
    wired["use"](FakePage(tracks=False))
    _run(vc.handle("explain what he just said", None))
    assert "Only the caption currently on screen is available" in wired["prompt"]


def test_no_drivable_browser_says_how_to_fix_it(wired, monkeypatch):
    wired["use"](None)
    monkeypatch.setattr("jarvis.integrations.web_browser.family", lambda: "firefox")
    reply = _run(vc.handle("explain what he just said", None))
    assert "restart the browser with control" in reply


def test_any_other_page_is_read_not_refused(wired):
    # Once: "not YouTube" meant "not mine". Now any page is explained from its own text, and a
    # question about a video on a page without one says so.
    wired["use"](FakePage(youtube=False))
    assert _run(vc.handle("explain what is on screen", None)).startswith("Because the squares")
    assert "Example page text" in wired["prompt"]
    assert "no video on this page" in _run(vc.handle("explain what he just said", None))


def test_play_that_does_not_advance_is_not_verified():
    page = FakePage(paused=True)
    page.frozen = True
    out = _run(yt.YouTube(page).control("play"))
    assert out["ok"] and not out["verified"] and "advancing" in out["reason"]
    page.frozen = False
    assert _run(yt.YouTube(page).control("play"))["verified"]


def test_player_error_is_reported_not_explained(wired):
    page = FakePage()
    page.error = "Something went wrong. Refresh or try again later."
    wired["use"](page)
    reply = _run(vc.handle("explain what he just said", None))
    assert "YouTube is showing an error" in reply and "Something went wrong" in reply
    assert "prompt" not in wired


def test_strong_tier_prefers_cloud_and_keeps_the_brain_last(monkeypatch):
    from jarvis import llm, providers
    from jarvis.config import Config
    c = Config()
    c.brain, c.groq_api_key, c.gemini_api_key, c.groq_model = "ollama", "g", "m", "retired/model"
    monkeypatch.delenv("JARVIS_STRONG_MODEL", raising=False)
    ids = [p.id for p in llm.candidates(c, "strong")]
    # Open-weight models on Groq first (each its own quota), then the configured model.
    assert ids[:4] == [*(f"groq:{m}" for m in providers.OPEN_STRONG), "groq:retired/model",
                       f"groq:{providers.DEFAULT_GROQ_MODEL}"]
    assert ids[-1] == f"ollama:{c.ollama_model}" and f"gemini:{c.gemini_model}" in ids
    assert [p.id for p in llm.candidates(c)] == [f"ollama:{c.ollama_model}"]
    monkeypatch.setenv("JARVIS_STRONG_MODEL", "openai/gpt-oss-120b")
    assert llm.candidates(c, "strong")[0].model == "openai/gpt-oss-120b"
