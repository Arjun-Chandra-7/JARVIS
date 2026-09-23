"""The parts under the conversation: media, privacy, streaming, the voice's fallbacks.

No audio hardware, no network model: players, PipeWire, the brain and the synthesiser are all
replaced, and the checks are on what the code decides and sends.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from jarvis.audio import conversation, media, voice_log
from jarvis.audio.conversation import ConversationSession, State
from jarvis.audio.speech_text import language_of as st_lang


# ----------------------------------------------------------------- media
@pytest.mark.parametrize("said, name", [
    ("pause", "pause"), ("Wait, pause it.", "pause"), ("pause the video", "pause"),
    ("rok do", "pause"), ("resume", "play"), ("play", "play"), ("next", "next"),
    ("previous video", "previous"),
])
def test_media_keys_are_recognised_whole(said, name):
    assert media.transport_command(said) == name


@pytest.mark.parametrize("said", ["play the first video", "pause and explain this part",
                                  "search for pythagoras", "next week's schedule"])
def test_requests_that_mention_media_are_not_media_keys(said):
    assert media.transport_command(said) is None


class Mixer:
    """pw-dump / wpctl / playerctl, as far as Media uses them. Two players: the one playing, and
    another that is not (the default player is the idle one, as it was on the live run)."""

    def __init__(self, status="Playing", obey=True):
        self.players = {"chromium.instance1": "Paused", "firefox.instance2": status}
        self.obey = obey
        self.volumes = {101: 0.8, 202: 1.0, 303: 0.5}
        self.commands = []

    def run(self, args, timeout=1.5):
        self.commands.append(args)
        if args[:2] == ["playerctl", "-l"]:
            return "\n".join(self.players) + "\n"
        if args[:2] == ["playerctl", "-p"] and args[3] == "status":
            return self.players[args[2]] + "\n"
        if args[:2] == ["playerctl", "-p"] and self.obey:
            self.players[args[2]] = {"pause": "Paused", "play": "Playing"}.get(args[3], self.players[args[2]])
            return ""
        if args[0] == "pw-dump":
            import os
            return json.dumps([
                {"id": 101, "info": {"state": "running", "props": {"media.class": "Stream/Output/Audio",
                                                                  "application.process.id": 1}}},
                {"id": 202, "info": {"state": "running", "props": {"media.class": "Stream/Output/Audio",
                                                                  "application.process.id": os.getpid()}}},
                {"id": 303, "info": {"state": "running", "props": {"media.class": "Stream/Output/Audio",
                                                                  "node.name": "jarvis_aec_playback"}}},
                {"id": 404, "info": {"state": "idle", "props": {"media.class": "Stream/Output/Audio"}}},
            ])
        if args[:2] == ["wpctl", "get-volume"]:
            return f"Volume: {self.volumes[int(args[2])]:.2f}\n"
        if args[:2] == ["wpctl", "set-volume"]:
            self.volumes[int(args[2])] = float(args[3])
        return ""


def test_ducking_lowers_only_other_apps_and_restores_them_exactly():
    mixer = Mixer()
    m = media.Media(run=mixer.run)
    assert m.duck() == 1
    assert mixer.volumes[101] == pytest.approx(0.8 * media.DUCK_TO, abs=0.01)
    assert mixer.volumes[202] == 1.0            # Jarvis's own voice is never ducked
    assert mixer.volumes[303] == 0.5            # nor the echo canceller
    assert m.restore() == 1
    assert mixer.volumes[101] == 0.8
    assert not m.ducked


def test_pause_goes_to_the_player_that_is_playing_and_play_resumes_that_one():
    mixer = Mixer("Playing")
    m = media.Media(run=mixer.run, sleep=lambda s: None)
    ok, said = m.transport("pause")
    assert ok and said == "Paused, sir." and m.paused_by_us
    assert ["playerctl", "-p", "chromium.instance1", "pause"] not in mixer.commands
    assert mixer.players["firefox.instance2"] == "Paused"
    ok, said = m.transport("play")
    assert ok and said == "Playing, sir." and mixer.players["firefox.instance2"] == "Playing"
    assert mixer.players["chromium.instance1"] == "Paused"        # never touched


def test_a_pause_the_player_ignored_is_not_reported_as_done():
    mixer = Mixer("Playing", obey=False)
    m = media.Media(run=mixer.run, sleep=lambda s: None)
    ok, said = m.transport("pause")
    assert not ok and "still playing" in said


def test_nothing_playing_is_already_paused():
    mixer = Mixer("Paused")
    m = media.Media(run=mixer.run, sleep=lambda s: None)
    assert m.transport("pause") == (True, "Nothing I can control is playing, sir.")


def test_no_player_is_said_plainly():
    m = media.Media(run=lambda args, timeout=1.5: "")
    ok, said = m.transport("pause")
    assert not ok and "Nothing is playing" in said


# ----------------------------------------------------------------- the state machine
def test_errors_keep_only_a_category_and_recover_to_listening():
    c = ConversationSession()
    c.wake()
    c.error("TimeoutError")
    assert c.state is State.ERROR and c.error_kind == "TimeoutError"
    assert c.recover() is State.WAKE_LISTENING and not c.active


def test_stop_is_not_goodbye():
    assert conversation.is_stop("stop") and conversation.is_stop("Wait.") and conversation.is_stop("ruko")
    assert not conversation.is_stop("stop the timer")
    assert conversation.is_session_end("that's all") and not conversation.is_session_end("stop")
    c = ConversationSession()
    c.wake()
    c.speaking()
    assert c.stopped() is State.FOLLOW_UP and c.active


def test_cancel_everything_and_go_on():
    assert conversation.is_cancel_all("cancel everything") and conversation.is_cancel_all("sab cancel karo")
    assert not conversation.is_cancel_all("cancel the email")
    assert conversation.is_continue("go on") and conversation.is_continue("haan, aage bolo")


def test_the_conversation_forgets_its_memory_when_it_ends():
    c = ConversationSession()
    c.wake()
    c.interrupted("the rest of the answer")
    c.language = "hinglish"
    c.end()
    assert c.interrupted_reply == "" and c.language == "" and c.state is State.IDLE


def test_dictation_outside_a_conversation_returns_to_the_wake_word():
    c = ConversationSession()
    c.listen_for_wake()
    c.dictation_started()
    assert c.dictation_ended() is State.WAKE_LISTENING


def test_the_state_is_published_for_the_other_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    c = ConversationSession(on_change=conversation.publish_to_file)
    c.wake()
    c.replied("Done.")
    assert conversation.read_published()["state"] == "follow_up"
    assert conversation.read_published()["in_conversation"] is True


def test_after_an_interruption_a_cough_is_not_a_request():
    c = ConversationSession()
    c.wake()
    c.speaking()
    c.interrupted("rest")
    c.endpointing()
    assert c.judge("hmm") == conversation.IGNORE
    assert c.judge("explain why") == conversation.ACT


def test_right_after_the_wake_word_everything_is_for_jarvis():
    c = ConversationSession()
    c.wake()
    c.endpointing()
    assert c.judge("hmm") == conversation.ACT


# ----------------------------------------------------------------- privacy
@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def test_transcripts_and_replies_are_word_counts_in_the_journal(runtime):
    assert voice_log.journal_line("heard", "message Papa that I'll be late") == "(6 words)"
    assert st_lang("The square of the hypotenuse.") == "en"     # "the" is not थे
    assert voice_log.journal_line("reply", "Sent to Papa.") == "(3 words)"
    line = voice_log.journal_line("phone", "WhatsApp from Asha: see you at the station")
    assert "station" not in line and "WhatsApp from Asha" in line


def test_diagnostics_show_words_masked_and_switch_themselves_off(runtime):
    voice_log.enable_diagnostics(1)
    shown = voice_log.journal_line("heard", "call +91 98765 43210 or mail a@b.co")
    assert "call" in shown and "98765" not in shown and "a@b.co" not in shown
    (runtime / "jarvis-voice-diagnostics").write_text(str(time.time() - 1))    # expired
    assert voice_log.journal_line("heard", "hello there") == "(2 words)"
    assert not (runtime / "jarvis-voice-diagnostics").exists()


def test_metrics_never_carry_what_was_said(runtime):
    voice_log.metric("turn", end_to_action_ms=180.0, transcript="message Papa I'm late",
                     reply="Sent.", reason="a long sentence someone said out loud in the room today")
    record = voice_log.recent("turn")[-1]
    assert "transcript" not in record and "reply" not in record
    assert len(record["reason"]) <= 40
    assert record["end_to_action_ms"] == 180.0


def test_the_voice_report_reads_the_metrics(runtime):
    from jarvis.audio import voice_report
    for ms in (150, 190, 240):
        voice_log.metric("barge_in", onset_to_stop_ms=float(ms), detector_ms=160.0)
    out = voice_report.render()
    assert "median    190 ms" in out


# ----------------------------------------------------------------- streaming
def _stream_client(monkeypatch, tmp_path, brain):
    from fastapi.testclient import TestClient

    from jarvis import webserver
    from jarvis.dedupe import Deduper
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(webserver.hud_state, "log_turn", lambda *a: None)
    monkeypatch.setattr("jarvis.dedupe.CHAT", Deduper(window_s=60))
    monkeypatch.setitem(webserver._agent, "a", brain)
    return TestClient(webserver.app)


class StreamingBrain:
    def __init__(self, pieces, reply):
        self.pieces, self.reply, self.on_reply_delta, self.calls = pieces, reply, None, 0

    async def send(self, _text):
        self.calls += 1
        for p in self.pieces:
            if self.on_reply_delta:
                self.on_reply_delta(p)
            await asyncio.sleep(0)
        return self.reply


def _lines(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_a_question_streams_its_answer(monkeypatch, tmp_path):
    brain = StreamingBrain(["Photosynthesis ", "makes sugar. ", "It needs light."],
                           "Photosynthesis makes sugar. It needs light.")
    client = _stream_client(monkeypatch, tmp_path, brain)
    items = _lines(client.post("/chat/stream", json={"message": "what is photosynthesis"}))
    assert [i["delta"] for i in items if "delta" in i] == brain.pieces
    assert items[-1] == {"reply": "Photosynthesis makes sugar. It needs light.", "streamed": True}


def test_an_action_is_never_streamed_before_it_is_checked(monkeypatch, tmp_path):
    brain = StreamingBrain(["Opened ", "YouTube."], "Opened YouTube, sir.")
    client = _stream_client(monkeypatch, tmp_path, brain)
    items = _lines(client.post("/chat/stream", json={"message": "open youtube"}))
    assert items == [{"reply": "Opened YouTube, sir.", "streamed": False}]


def test_the_same_utterance_twice_is_acted_on_once(monkeypatch, tmp_path):
    brain = StreamingBrain([], "Done, sir.")
    client = _stream_client(monkeypatch, tmp_path, brain)
    for _ in range(2):
        client.post("/chat/stream", json={"message": "open youtube", "event_id": "e1"})
    assert brain.calls == 1


def test_the_voice_client_reads_the_stream(monkeypatch):
    from jarvis.agent import remote

    lines = [json.dumps({"delta": "One. "}), json.dumps({"delta": "Two."}),
             json.dumps({"reply": "One. Two.", "streamed": True})]

    class Response:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *a):
            return False

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return Stream()

    monkeypatch.setattr(remote.httpx, "AsyncClient", Client)
    got = []
    agent = remote.RemoteAgent("voice")
    agent.on_reply_delta = got.append
    assert asyncio.run(agent.send("explain it")) == "One. Two."
    assert got == ["One. ", "Two."]


# ----------------------------------------------------------------- the voice itself
def test_a_code_block_is_never_cut_into_sentences_while_streaming():
    from jarvis.audio.local_tts import sentences_as_they_arrive
    out = list(sentences_as_they_arrive(["Here. ", "```py\nx = 1. y = 2. ", "z = 3\n``` ", "Done."]))
    assert out[0] == "Here."
    assert any("x = 1. y = 2." in piece for piece in out)          # kept whole, not split


def test_hindi_sentences_end_at_the_danda():
    from jarvis.audio.local_tts import sentences_as_they_arrive
    out = list(sentences_as_they_arrive(["यह पहला वाक्य है। ", "यह दूसरा है।"]))
    assert out == ["यह पहला वाक्य है।", "यह दूसरा है।"]


def test_hindi_goes_to_the_hindi_voice_through_the_hindi_phonemiser(monkeypatch):
    from jarvis.audio import kokoro_tts
    made = []

    class Model:
        tokenizer = SimpleNamespace(phonemize=lambda text, lang: f"(en)skwˈeəɹ(hi) {lang}:{text}")

        def create(self, text, voice, speed, lang=None, is_phonemes=False):
            made.append((voice, lang, is_phonemes, text))
            return [0.0] * 10, 24000

    model = Model()
    kokoro_tts._create(model, "Is step mein square add karte hain.", "bm_daniel", 1.0)
    kokoro_tts._create(model, "The square of the hypotenuse.", "bm_daniel", 1.0)
    (voice, _lang, phonemes, text), english = made
    assert voice == kokoro_tts.HINDI_VOICE and phonemes is True
    assert "(en)" not in text and "(hi)" not in text and "करते" in text
    assert english[:3] == ("bm_daniel", "en-gb", False)


def test_the_fallback_voice_says_it_is_the_fallback(monkeypatch):
    from jarvis.audio import local_tts
    said = []
    monkeypatch.setattr(local_tts, "_better_voice_available", lambda: True)
    monkeypatch.setattr(local_tts, "_get_voice", lambda _p: object())
    monkeypatch.setattr(local_tts, "_synth_chunk_api", lambda _v, text: (said.append(text) or b"\1\0", 22050))

    def broken(*a, **k):
        raise RuntimeError("kokoro failed to load")
        yield

    monkeypatch.setattr("jarvis.audio.kokoro_tts.synth_stream", broken)
    local_tts._fallback["announced"] = False
    list(local_tts.synth_stream("Done, sir.", "piper.onnx"))
    assert said[0].startswith(local_tts.FALLBACK_NOTICE)
    said.clear()
    list(local_tts.synth_stream("Done again.", "piper.onnx"))
    assert local_tts.FALLBACK_NOTICE not in said[0]                  # said once, not every line


def test_the_english_fallback_voice_does_not_mangle_hindi(monkeypatch):
    from jarvis.audio import local_tts
    said = []
    monkeypatch.setattr(local_tts, "_better_voice_available", lambda: False)
    monkeypatch.setattr(local_tts, "_get_voice", lambda _p: object())
    monkeypatch.setattr(local_tts, "_synth_chunk_api", lambda _v, text: (said.append(text) or b"\1\0", 22050))
    list(local_tts.synth_stream("यह पहला वाक्य है। यह दूसरा है।", "piper.onnx"))
    assert said == [local_tts._HINDI_UNSAYABLE]


def test_acknowledgements_come_from_the_cache(monkeypatch):
    from jarvis.audio import local_tts
    monkeypatch.setitem(local_tts._ACK_CACHE, "Done, sir.", (b"\1\0" * 10, 24000))
    monkeypatch.setattr(local_tts, "_better_voice_available",
                        lambda: (_ for _ in ()).throw(AssertionError("synthesised a cached line")))
    assert list(local_tts.synth_stream("Done, sir.", "x")) == [(b"\1\0" * 10, 24000)]
