"""Wake once, then keep going: the follow-up window and what counts as being spoken to."""
import pytest

from jarvis.audio.conversation import ACT, END, IGNORE, ConversationSession, State
from jarvis.audio.voice_session import instant_intercept


def _session():
    s = ConversationSession(window_s=8)
    s.wake()
    return s


def test_youtube_flow_needs_the_wake_word_once():
    s = _session()
    assert s.judge("open YouTube") == ACT
    s.thinking(); s.speaking()
    for follow_up in ["search for Pythagoras theorem", "play the first video", "pause"]:
        assert s.replied("Done.") is State.FOLLOW_UP_WINDOW
        assert s.judge(follow_up) == ACT, follow_up
        s.thinking()
    assert s.turns == 3


@pytest.mark.parametrize("noise", ["hmm", "uh", "okay", "yeah", "Thanks for watching!", "you", "[Music]",
                                   "achha", "", "   ", "the"])
def test_room_noise_in_the_window_is_ignored(noise):
    s = _session()
    s.replied("Opened YouTube.")
    assert s.judge(noise) == IGNORE


@pytest.mark.parametrize("said", ["that's all", "Thanks Jarvis", "okay thank you, that's it", "bas",
                                  "never mind", "No thanks.", "bye"])
def test_saying_goodbye_ends_it(said):
    s = _session()
    s.replied("Done.")
    assert s.judge(said) == END


def test_thats_all_inside_a_request_is_a_request():
    s = _session()
    s.replied("Done.")
    assert s.judge("that's all I needed for the essay, now open Docs") == ACT


def test_after_the_wake_word_even_one_word_is_meant():
    s = _session()
    assert s.judge("weather") == ACT


def test_a_question_makes_a_one_word_answer_count():
    s = _session()
    s.replied("Which Nikhil — Nikhil Painter or Nikhil Jain?")
    assert s.judge("Painter") == ACT
    s.replied('Ready to send to Papa on WhatsApp: "hi". Say "send it" to confirm.')
    assert s.judge("yes") == ACT
    s.replied("Done.")
    assert s.judge("yes") == IGNORE


def test_silence_closes_the_conversation():
    s = _session()
    s.replied("Done.")
    assert s.silence() is State.IDLE and not s.active


def test_a_runaway_conversation_ends():
    s = ConversationSession(max_turns=3)
    s.wake()
    states = [s.replied("ok") for _ in range(3)]
    assert states[-1] is State.IDLE


def test_barge_in_goes_back_to_listening():
    s = _session()
    s.speaking()
    assert s.interrupted() is State.ACTIVE_LISTENING
    assert s.history[-2:] == ["interrupted", "active_listening"]


def test_sleeping_ignores_the_wake_word_until_woken():
    s = ConversationSession()
    s.sleep()
    assert s.wake() is State.SLEEPING
    s.wake_from_sleep()
    assert s.wake() is State.ACTIVE_LISTENING


# ----------------------------------------------------------------- instant intercepts

@pytest.mark.parametrize("said,want", [
    ("status report", "status"), ("wake up", "status"), ("system pulse", "status"),
    ("open my phone", "mirror_phone"), ("mirror phone please", "mirror_phone"),
    ("ring my phone", "ring_phone"), ("where is my phone", "ring_phone"),
    # These were hijacked by substring matching.
    ("what is impulse", None), ("explain impulse and momentum", None), ("open phonepe", None),
    ("my pulse rate is high, is that normal", None), ("the audience clapped", None),
    ("call my phone company", None),
])
def test_instant_intercepts_match_whole_requests_only(said, want):
    assert instant_intercept(said) == want
