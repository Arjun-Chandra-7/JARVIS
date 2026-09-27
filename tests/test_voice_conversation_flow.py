"""The voice loop's conversation, driven through its real turn-handling code.

The microphone, the speaker and the brain are replaced; everything between them — the
conversation state, "is this for us", the fast path, barge-in handling, dictation resume, the
goodbye — is the code that runs in the service. Transcripts are scripted, so this proves the
wiring, not the acoustics: whether the room's audio *produces* these transcripts is what the
live checks are for.
"""
import asyncio
from types import SimpleNamespace

import pytest

from jarvis.audio import voice_session
from jarvis.audio.conversation import ConversationSession, State


class Brain:
    def __init__(self, replies=None):
        self.asked: list[str] = []
        self.replies = replies or {}
        self.on_reply_delta = None
        self.cancelled = 0
        self.ended = 0

    async def send(self, text):
        self.asked.append(text)
        for key, reply in self.replies.items():
            if key in text.lower():
                return reply
        return "Done, sir."

    async def cancel_tasks(self):
        self.cancelled += 1
        return "1 queued task was cancelled."

    async def end_conversation(self):
        self.ended += 1


class Media:
    def __init__(self):
        self.calls: list[str] = []
        self.ducked = False

    def transport(self, name):
        self.calls.append(name)
        return True, {"pause": "Paused, sir.", "play": "Playing, sir."}.get(name, "Next, sir.")

    def duck(self):
        self.ducked = True
        return 1

    def restore(self):
        self.calls.append("restore")
        self.ducked = False
        return 1


def make_session(script, monkeypatch, barge_on_first_speak=None):
    """A VoiceSession with its devices replaced. ``script``: transcripts, in the order the
    microphone would produce them (None = nothing said before the window closed)."""
    monkeypatch.setattr("jarvis.integrations.coding.active_context", lambda: {})
    monkeypatch.setattr("jarvis.power.asleep", lambda: False)
    s = voice_session.VoiceSession.__new__(voice_session.VoiceSession)
    s.config = SimpleNamespace(follow_up_s=8, enable_followup=True, kde_device_id="",
                               user_name="Test")
    s.backend = "cloud"                       # the brain is asked for whole replies
    s.events = []
    s.on_event = lambda kind, text="": s.events.append((kind, text))
    s._conversation = ConversationSession(window_s=8)
    s._media = Media()
    s._media_on = False
    s.aec_active = False
    s._dictating = False
    s._flow = None
    s._preempted = False
    s._barge = None
    s._played = []
    s._speech_ended_at = 0.0
    s.spoken = []
    s.recorded_prefixes = []
    pending = list(script)

    def record(wait_s, prefix=None):
        if prefix:
            s.recorded_prefixes.append(prefix)
        text = pending.pop(0) if pending else None
        if text is not None:
            # as the real capture does when speech starts
            s._conversation.endpointing() if s._conversation.state is not State.DICTATION else None
        return text

    def speak(text, force=False):
        s.spoken.append(text)
        if barge_on_first_speak and len(s.spoken) == 1:
            # The monitor heard the person two sentences into a four-sentence answer.
            s._played = ["one", "two"]
            s._barge = {"onset_at": 0.0, "triggered_at": 0.24, "stopped_at": 0.26,
                        "frames": [[0] * 1280] * 5, "policy": "aec", "detector_ms": 160.0,
                        "during": "speaking"}
        else:
            s._played = []

    s._record_transcript = record
    s._speak = speak
    s._flush_mic = lambda frames=8: None
    s.stop_speaking = lambda: False
    s._augment = lambda t: t
    return s


def run_conversation(s, brain, first):
    s._conversation.wake()
    return asyncio.run(s._turns(brain, first))


# ----------------------------------------------------------------- one wake, many turns
def test_one_wake_word_then_a_whole_lecture_flow_without_it(monkeypatch):
    s = make_session(["Search for Pythagoras theorem.", "Play the first video.", "Wait, pause it.",
                      "Ab ye step Hinglish mein samjhao.", "That's all."], monkeypatch)
    brain = Brain({"youtube": "YouTube is open, sir.",
                   "pythagoras": "Here are the results for Pythagoras theorem, sir.",
                   "first video": "Playing the first video, sir.",
                   "samjhao": "Is step mein hum dono sides ka square add karte hain."})
    ended_with_goodbye = run_conversation(s, brain, "Jarvis, open YouTube.")
    # Four requests reached the brain; "pause it" never left the voice process.
    assert brain.asked == ["Jarvis, open YouTube.", "Search for Pythagoras theorem.",
                           "Play the first video.", "Ab ye step Hinglish mein samjhao."]
    assert s._media.calls == ["pause"]
    assert "Paused, sir." in s.spoken
    assert ended_with_goodbye is True
    assert s._conversation.history.count("wake_detected") == 1      # one wake for all of it
    assert s._conversation.history.count("follow_up") >= 4


def test_hinglish_follow_ups_are_acted_on_and_remembered_as_hinglish(monkeypatch):
    s = make_session(["photosynthesis kya hota hai", None], monkeypatch)
    brain = Brain()
    run_conversation(s, brain, "open youtube")
    assert brain.asked[-1] == "photosynthesis kya hota hai"


def test_a_hindi_follow_up_in_devanagari_is_acted_on(monkeypatch):
    s = make_session(["अगला वीडियो चलाओ", None], monkeypatch)
    brain = Brain()
    run_conversation(s, brain, "open youtube")
    assert brain.asked[-1] == "अगला वीडियो चलाओ"


def test_the_window_closing_quietly_ends_without_a_goodbye(monkeypatch):
    s = make_session([None], monkeypatch)
    assert run_conversation(s, Brain(), "open youtube") is False
    assert s._conversation.state is State.IDLE


def test_a_cough_in_the_follow_up_window_is_not_a_request(monkeypatch):
    s = make_session(["hmm", None], monkeypatch)
    brain = Brain()
    run_conversation(s, brain, "open youtube")
    assert brain.asked == ["open youtube"]


# ----------------------------------------------------------------- stop is not goodbye
def test_stop_cuts_and_keeps_the_conversation_open(monkeypatch):
    s = make_session(["stop", "search for triangles", None], monkeypatch)
    brain = Brain()
    assert run_conversation(s, brain, "explain pythagoras") is False
    assert brain.asked == ["explain pythagoras", "search for triangles"]     # still listening
    assert "stop" not in [a.lower() for a in brain.asked]


@pytest.mark.parametrize("goodbye", ["That's all.", "bas", "bye", "Thank you Jarvis."])
def test_goodbyes_end_the_conversation(monkeypatch, goodbye):
    s = make_session([goodbye], monkeypatch)
    assert run_conversation(s, Brain(), "open youtube") is True
    assert s.spoken[-1] == "Alright, sir."


def test_cancel_everything_cancels_the_jobs_and_stays_in_the_conversation(monkeypatch):
    s = make_session(["cancel everything", None], monkeypatch)
    brain = Brain()
    run_conversation(s, brain, "research black holes")
    assert brain.cancelled == 1
    assert any(t.startswith("Cancelled, sir. 1 queued task") for t in s.spoken)


# ----------------------------------------------------------------- barge-in
def test_talking_over_jarvis_is_heard_and_routed_with_the_conversation(monkeypatch):
    s = make_session(["wait, explain why", None], monkeypatch, barge_on_first_speak=True)
    brain = Brain({"pythagoras": "The theorem states one. Then two. Then three. Then four."})
    run_conversation(s, brain, "explain pythagoras")
    assert brain.asked == ["explain pythagoras", "wait, explain why"]
    assert s.recorded_prefixes and len(s.recorded_prefixes[0]) == 5    # the onset was kept
    assert "interrupted" in s._conversation.history


def test_go_on_finishes_the_interrupted_answer(monkeypatch):
    s = make_session(["go on", None], monkeypatch, barge_on_first_speak=True)
    brain = Brain({"pythagoras": "One is first. Two is second. Three is third. Four is last."})
    run_conversation(s, brain, "explain pythagoras")
    assert s.spoken[-1] == "Three is third. Four is last."
    assert brain.asked == ["explain pythagoras"]


def test_a_false_interruption_finishes_the_sentence(monkeypatch):
    # The monitor fired, but nothing intelligible followed: the TV, a cough.
    s = make_session([None, None], monkeypatch, barge_on_first_speak=True)
    brain = Brain({"pythagoras": "One is first. Two is second. Three is third. Four is last."})
    run_conversation(s, brain, "explain pythagoras")
    assert s.spoken[1] == "Three is third. Four is last."


# ----------------------------------------------------------------- dictation
def test_dictation_mid_conversation_hands_the_conversation_back(monkeypatch):
    s = make_session([], monkeypatch)
    c = s._conversation
    c.wake()
    c.replied("YouTube is open.")
    c.dictation_started()
    assert c.state is State.DICTATION and not c.active
    c.dictation_ended()
    assert c.state is State.FOLLOW_UP          # the wake listener resumes it without the name


def test_the_wake_wait_resumes_a_conversation_after_dictation(monkeypatch):
    s = make_session([], monkeypatch)
    s._conversation.state = State.FOLLOW_UP
    s._coord = SimpleNamespace(listen_for_wake=lambda: None)
    s._events = asyncio.Queue()
    kind, _ = asyncio.run(s._wait_for_wake_or_event())
    assert kind == "resume"


# ----------------------------------------------------------------- failure
def test_a_failing_turn_restores_the_video_and_leaves_a_known_state(monkeypatch):
    s = make_session([], monkeypatch)
    s._media_on = True
    s._media.ducked = True

    def broken(wait_s, prefix=None):
        raise RuntimeError("microphone vanished")

    s._record_transcript = broken
    with pytest.raises(RuntimeError):
        asyncio.run(s._conversation_from_wake(Brain()))
    assert s._conversation.state is State.IDLE
    import time
    time.sleep(0.05)                                   # the restore runs on a thread
    assert "restore" in s._media.calls


def test_technical_errors_are_summarised_not_read_out():
    assert voice_session.speakable("[groq error] Completions.create() got 2 values") == \
        "Something went wrong on my side, sir. The details are on screen."
    assert voice_session.speakable("[voice can't reach the brain — is --web running? x]").startswith(
        "I can't reach my brain")
    assert voice_session.speakable("Opened YouTube, sir.") == "Opened YouTube, sir."


def test_a_goodbye_forgets_the_conversation_context(monkeypatch):
    s = make_session(["That's all."], monkeypatch)
    brain = Brain()
    s._record_transcript = (lambda seq: (lambda wait_s, prefix=None: seq.pop(0) if seq else None))(
        ["open youtube", "That's all."])
    asyncio.run(s._conversation_from_wake(brain))
    assert brain.ended == 1


def test_a_false_wake_over_a_video_is_not_apologised_for(monkeypatch):
    s = make_session([None], monkeypatch)
    s._media_on = True
    s._capture_problem = ""
    asyncio.run(s._conversation_from_wake(Brain()))
    assert s.spoken == []


def test_a_false_interruption_while_thinking_keeps_the_request(monkeypatch):
    # The TV talked while the brain was still working, and the request was dropped on the spot.
    # When nothing said turns out to be for Jarvis, it is asked again under the same event id —
    # the backend then returns the first answer instead of doing the work twice.
    s = make_session([None, None], monkeypatch)
    ids = []

    class ThinkingBrain(Brain):
        async def send(self, text):
            ids.append(self.event_id)
            self.asked.append(text)
            if len(self.asked) == 1:
                s._barge = {"onset_at": 0.0, "triggered_at": 0.4, "stopped_at": 0.4,
                            "frames": [[0] * 1280], "policy": "raw", "detector_ms": 400.0,
                            "during": "thinking"}
                return ""                      # dropped before an answer came back
            return "Here is the answer, sir."

    brain = ThinkingBrain()
    run_conversation(s, brain, "research black holes")
    assert brain.asked == ["research black holes", "research black holes"]
    assert ids[0] and ids[0] == ids[1]
    assert "Here is the answer, sir." in s.spoken
