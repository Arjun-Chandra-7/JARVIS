"""The live failure: "Jarvis, अब क्या बोला, यह समझाना?" answered about the Claude terminal and CPU.

Every current-video pattern was in Latin letters, so the Devanagari question matched nothing and
went to the model with its memory tool, which read back an older conversation. These go through
/chat, as voice and the overlay do, and check where the answer came from — the open video's
transcript, or an honest "I can't see it" — and that the model and memory were never reached.
"""
import pytest
from fastapi.testclient import TestClient

from jarvis import route_log, video_command as vc, webserver
from jarvis.audio.conversation import ACT, ConversationSession
from jarvis.config import CONFIG
from jarvis.dedupe import Deduper
from jarvis.screen import youtube as yt
from jarvis.screen_context import _PAGE as _PAGE_SCRIPT

TRANSCRIPT = [
    {"start": 50.0, "dur": 6.0, "text": "take a right angled triangle"},
    {"start": 56.0, "dur": 6.0, "text": "the side opposite the right angle is the hypotenuse"},
    {"start": 62.0, "dur": 6.0, "text": "its square equals the sum of the squares of the other two sides"},
]

PHRASES = [
    "अब क्या बोला, समझाओ",
    "अभी क्या कहा?",
    "यह क्या समझा रहा है?",
    "ये वाला part समझाओ",
    "अभी वाला step समझाओ",
    "Abhi kya bola, samjhao",
    "Ye part kya tha?",
    "Ye step samjha",
    "What did he just say?",
    "Explain this part",
    # exactly as the recogniser wrote them on the machine
    "Jarvis, अब क्या बोला यह समजाना?",
    "अब क्या बोला, यह समझाना?",
]


class Page:
    kind = "fake"

    def __init__(self, *, youtube=True, tracks=True, caption="", fresh=True, time=66.0, broken=False):
        self.youtube, self.tracks, self.caption, self.fresh = youtube, tracks, caption, fresh
        self.time, self.broken = time, broken

    async def run(self, body, args=None, timeout=20.0):
        if self.broken:
            raise ConnectionError("tab went away")
        if body is _PAGE_SCRIPT:
            return {"url": "https://docs.example/notes", "title": "Notes", "selection": "",
                    "in_view": "Notes about photosynthesis", "body": "Notes about photosynthesis", "video": None}
        if body is yt._STATE:
            if not self.youtube:
                return {"youtube": False, "url": "https://docs.example", "title": "Notes"}
            return {"youtube": True, "url": "https://www.youtube.com/watch?v=pyth10", "videoId": "pyth10",
                    "fresh": self.fresh, "title": "Pythagoras Theorem | Class 10", "channel": "Maths",
                    "time": self.time, "duration": 600.0, "paused": False, "chapter": "",
                    "liveCaption": self.caption, "ad": False, "playerError": "",
                    "tracks": [{"url": "https://yt/timedtext?x", "lang": "en", "kind": ""}] if self.tracks else []}
        if body is yt._FETCH_TRACK:
            return {"ok": True, "segments": TRANSCRIPT}
        if body is yt._PANEL:
            return {"ok": False, "reason": "no transcript button"}
        if body is yt._CONTROL:
            return {"ok": True, "paused": True, "time": self.time}
        raise AssertionError("unexpected script")


class Brain:
    def __init__(self):
        self.model_turns = []
        self.command_session = "voice"

    async def send(self, text):
        from jarvis.commands import handle
        direct = await handle(text, CONFIG, self.command_session)
        if direct is not None:
            return direct
        self.model_turns.append(text)
        return "I recall that the Claude terminal was checked earlier."


@pytest.fixture
def screen(monkeypatch, tmp_path):
    from jarvis.llm import Completion
    seen = {"prompts": []}

    async def fake_complete(system, prompt, config=None, temperature=0.2, timeout=60.0, strength="default"):
        seen["prompts"].append(prompt)
        seen["system"] = system
        return Completion(text="Teacher अभी बता रहा था कि hypotenuse का square बाकी दो sides के squares के sum के बराबर है।",
                          provider="groq:test", quality="strong")

    def no_memory(*_a, **_k):
        raise AssertionError("episodic memory consulted for a current-screen question")

    monkeypatch.setattr("jarvis.llm.complete_detailed", fake_complete)
    monkeypatch.setattr("jarvis.memory.search.recall", no_memory)
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("jarvis.power.asleep", lambda: False)
    monkeypatch.setattr("jarvis.context._CURRENT", "local")
    monkeypatch.setattr("jarvis.dedupe.CHAT", Deduper(window_s=0))
    route_log.RECENT.clear()
    brain = Brain()
    monkeypatch.setitem(webserver._agent, "a", brain)
    client = TestClient(webserver.app)

    def use(page):
        monkeypatch.setattr("jarvis.screen.page.active_page", lambda: page)

    def ask(text, event_id=""):
        return client.post("/chat", json={"message": text, "session_id": "voice",
                                          "event_id": event_id}).json()["reply"]

    seen.update(use=use, ask=ask, brain=brain)
    use(Page())
    return seen


def _last_screen_event():
    return [r for r in route_log.RECENT if str(r.get("intent", "")).startswith("screen.")][-1]


@pytest.mark.parametrize("said", PHRASES)
def test_every_phrasing_is_the_current_video(said):
    from jarvis.commands import clean_text
    assert vc.intent(clean_text(said)) == "just_said"


@pytest.mark.parametrize("said", PHRASES)
def test_answered_from_the_transcript_never_from_memory(screen, said):
    reply = screen["ask"](said)
    assert screen["brain"].model_turns == []
    assert "Claude terminal" not in reply
    event = _last_screen_event()
    assert event["context"] == "youtube_transcript" and event["memory"] == "not_consulted"
    assert event["video_id"] == "pyth10" and event["window"] and event["position"] == "1:06"
    prompt = screen["prompts"][-1]
    assert "its square equals the sum" in prompt and "Pythagoras Theorem" in prompt


def test_current_screen_outranks_recall_words(screen):
    assert vc.intent("remember what he just said? explain it") == "just_said"
    assert vc.intent("what did he say in yesterday's video") is None     # the past is memory's
    screen["ask"]("yaad hai abhi kya bola? samjhao")
    assert screen["brain"].model_turns == []


def test_devanagari_gets_natural_hinglish(screen):
    screen["ask"]("अब क्या बोला, समझाओ")
    p = screen["prompts"][-1]
    assert "Hinglish written in Devanagari" in p and "Not formal or translated Hindi" in p
    assert _last_screen_event()["lang"] == "hi"


def test_roman_hinglish_gets_hinglish(screen):
    screen["ask"]("Abhi kya bola, samjhao")
    assert "Hinglish (Roman script)" in screen["prompts"][-1]


def test_explicit_pure_hindi_wins(screen):
    screen["ask"]("अभी क्या कहा? शुद्ध हिंदी में समझाओ")
    assert "pure, natural Hindi" in screen["prompts"][-1]
    assert vc.reply_language("abhi kya bola, in English please") == "en"


def test_english_stays_english(screen):
    screen["ask"]("What did he just say?")
    assert "Answer in English." in screen["prompts"][-1]


def test_transcript_unavailable_is_said_in_hindi_and_nothing_is_invented(screen):
    screen["use"](Page(tracks=False, caption=""))
    reply = screen["ask"]("अब क्या बोला, समझाओ")
    assert reply == "मुझे अभी वीडियो का transcript नहीं मिल रहा। Captions on करके फिर बोलो।"
    assert screen["prompts"] == [] and screen["brain"].model_turns == []
    assert _last_screen_event()["action"] == "no_transcript"


def test_captions_off_in_english_too(screen):
    screen["use"](Page(tracks=False, caption=""))
    reply = screen["ask"]("Explain what he just said")
    assert "Turn captions on" in reply and screen["prompts"] == []


def test_a_page_without_a_video(screen):
    screen["use"](Page(youtube=False))
    reply = screen["ask"]("अभी क्या कहा?")
    assert "कोई video नहीं है" in reply
    assert screen["prompts"] == [] and screen["brain"].model_turns == []
    assert _last_screen_event()["context"] == "page_no_video"


def test_explain_this_on_any_page_uses_that_page(screen):
    screen["use"](Page(youtube=False))
    screen["ask"]("इसे समझाओ")
    assert "Notes about photosynthesis" in screen["prompts"][-1]
    assert _last_screen_event()["context"] == "page_view" and screen["brain"].model_turns == []


def test_stale_player_is_not_explained(screen):
    screen["use"](Page(fresh=False))
    reply = screen["ask"]("Abhi kya bola, samjhao")
    assert "badla" in reply and screen["prompts"] == []
    assert _last_screen_event()["context"] == "stale"


def test_browser_adapter_unavailable(screen):
    screen["use"](None)
    reply = screen["ask"]("अब क्या बोला, समझाओ")
    assert "browser नहीं पढ़ पा रहा" in reply and screen["brain"].model_turns == []


def test_browser_that_stops_answering(screen):
    screen["use"](Page(broken=True))
    reply = screen["ask"]("अब क्या बोला, समझाओ")
    assert "browser" in reply and screen["prompts"] == [] and screen["brain"].model_turns == []


def test_model_unavailable_gives_the_transcript_not_a_guess(screen, monkeypatch):
    from jarvis.llm import Completion

    async def down(*_a, **_k):
        return Completion(text="", provider="", quality="none", failures=["groq: 503"])

    monkeypatch.setattr("jarvis.llm.complete_detailed", down)
    reply = screen["ask"]("What did he just say?")
    assert "its square equals the sum" in reply and screen["brain"].model_turns == []


def test_screen_question_that_no_handler_answers_never_reaches_the_model(screen, monkeypatch):
    async def passes(*_a, **_k):
        return None

    monkeypatch.setattr(vc, "handle", passes)
    reply = screen["ask"]("अभी क्या कहा?")
    assert "guess नहीं करूँगा" in reply and screen["brain"].model_turns == []
    assert _last_screen_event()["memory"] == "not_consulted"


def test_follow_up_without_the_wake_word(screen):
    s = ConversationSession(window_s=8)
    s.wake()
    s.replied("Teacher अभी बता रहा था…")
    assert s.judge("अभी क्या कहा?") == ACT
    assert s.judge("ye step samjha") == ACT
    screen["ask"]("अभी क्या कहा?")
    assert _last_screen_event()["context"] == "youtube_transcript"


def test_the_same_utterance_is_explained_once(screen):
    screen["ask"]("अब क्या बोला, समझाओ", event_id="utt-7")
    screen["ask"]("अब क्या बोला, समझाओ", event_id="utt-7")
    assert len(screen["prompts"]) == 1


def test_tutor_is_told_not_to_guess(screen):
    screen["ask"]("What did he just say?")
    assert "never guess" in screen["system"] and "standard explanation" not in screen["system"]
