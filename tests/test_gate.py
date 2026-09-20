"""Work out what the person wants before reaching for anything that changes the world.

Every case here is from the log, verbatim:

    you> See you, daddy.              jarvis> Message sent to Daddy.
    you> Say Daddy.                   jarvis> Your machine is running smoothly.
    you> dictation, go Don.           jarvis> Image generated. Ready.
    you> generate an image of a       jarvis> Image generated. Ready.
"""
from __future__ import annotations

import pytest

from jarvis.agent import gate


def _schemas():
    from jarvis.agent.groq_tools import build_registry
    from jarvis.config import Config
    schemas, _ = build_registry(Config(), None, None)
    return schemas


def _names(schemas):
    return {s["function"]["name"] for s in schemas}


# ------------------------------------------------------------------ nothing was asked for
@pytest.mark.parametrize("said", [
    "See you, daddy.", "Say Daddy.", "dictation, go Don.", "thanks jarvis",
    "how are you", "what is the weather", "nice one",
])
def test_chat_cannot_reach_anything_that_changes_the_world(said):
    offered = _names(gate.allowed(_schemas(), said))
    for dangerous in ("whatsapp_send", "generate_image", "message_person", "place_call",
                      "google_email_send", "run_bash", "write_file", "open_app"):
        assert dangerous not in offered, f"{said!r} could still call {dangerous}"


@pytest.mark.parametrize("said", [
    "open netflix", "send a message to mum saying hello", "draw me a fox",
    "set the volume to 40", "make me a picture of a dragon",
])
def test_a_real_request_keeps_every_tool(said):
    assert len(gate.allowed(_schemas(), said)) == len(_schemas())


def test_a_question_can_still_look_things_up():
    """The problem was never questions — it was that chat could reach the send button."""
    offered = _names(gate.allowed(_schemas(), "what is on my calendar"))
    assert "google_agenda" in offered
    assert "whatsapp_send" not in offered


def test_plain_conversation_gets_no_tools_at_all():
    """A model holding a tool will find a reason to use it."""
    for said in ("hey jarvis", "how are you", "who are you", "goodnight", "thanks"):
        assert gate.allowed(_schemas(), said) == []


def test_a_greeting_in_front_of_a_request_is_still_a_request():
    assert len(gate.allowed(_schemas(), "hey jarvis open netflix")) == len(_schemas())


# ------------------------------------------------------------------ half a sentence
@pytest.mark.parametrize("said", [
    "generate an image of a", "send a message to", "open the", "draw me the",
    "play some music by", "put it on the", "tell her that",
])
def test_a_sentence_that_stops_mid_phrase_is_not_acted_on(said):
    """Only the shape is checked, not the sense. "Remind me to call" is unfinished to a person
    and complete to a word list, and no amount of pattern is going to close that gap — the guard
    catches the sentence that ends on a dangling article or preposition, which is what a clipped
    capture window actually produces."""
    assert gate.looks_unfinished(said) is True
    assert gate.wants_something_done(said) is False


@pytest.mark.parametrize("said", [
    "generate an image of a fox", "open netflix", "send a message to mum saying hi",
    "what is the time", "draw me the mona lisa",
])
def test_a_whole_sentence_is_not_mistaken_for_half_of_one(said):
    assert gate.looks_unfinished(said) is False


def test_the_answer_to_half_a_sentence_is_a_question():
    said = gate.ask_for_the_rest("generate an image of a")
    assert "?" in said and "trailed off" in said


# ------------------------------------------------------------------ the classification itself
def test_read_only_is_an_allow_list_not_a_guess():
    """Forgetting a reading tool costs nothing worse than "not while we're chatting".
    Forgetting a writing one sends a message to somebody."""
    assert gate.is_read_only("read_file") and gate.is_read_only("web_search")
    for invented in ("send_the_nukes", "whatsapp_send", "delete_everything", "generate_image"):
        assert not gate.is_read_only(invented)


def test_every_named_read_only_tool_actually_exists():
    """A name that drifts out of the registry silently narrows what chat is allowed to do."""
    existing = _names(_schemas())
    # Tools behind optional integrations may be absent; the ones present must at least match.
    unknown = {n for n in gate.READ_ONLY if n not in existing}
    assert not unknown & {"read_file", "web_search", "system_stats", "recall"}


def test_two_word_commands_still_work():
    """"Stop" and "open netflix" are short, and they are requests."""
    assert gate.wants_something_done("stop") is True
    assert gate.wants_something_done("open netflix") is True


# --------------------------------------------------------------- a question is not a job
#
# Every spoken turn used to carry the same note: "DO the action with a tool first." Told to act
# when there is no action, a model describes one. Straight from the saved history:
#
#     you> What are their opinions on Elon Musk?
#     jarvis> I'll look up some recent articles about Elon Musk's opinions. It might take a
#             moment. How can I assist you further?
#
# No tool was called and no moment was taken, because the turn had already ended.

import pytest

from jarvis.agent import action_claims, remote


@pytest.mark.parametrize("said", [
    "What are their opinions on Elon Musk?",
    "What colour is the sky?",
    "what do you think about AI safety",
    "what are some good names for a tech company",
    "why is the sea salty",
    "who was Ramanujan",
])
def test_a_question_is_told_to_answer_not_to_act(said):
    note = remote.voice_note(said)
    assert "DO the action" not in note, f"told to act on a question: {said!r}"
    assert "Answer it yourself" in note


@pytest.mark.parametrize("said", [
    "open netflix",
    "send a message to mum saying I am late",
    "play something by MC Square",
    "turn the brightness down",
])
def test_a_request_is_still_told_to_act(said):
    assert "DO the action" in remote.voice_note(said)


def test_the_reply_that_started_this_is_caught_as_a_promise():
    """Verbatim from the history. It reads like progress and is a dead end."""
    reply = ("I'll look up some recent articles about Elon Musk's opinions. It might take a "
             "moment. How can I assist you further?")
    assert action_claims.promises_without_acting(reply)


def test_answering_a_question_outright_is_not_a_promise():
    """The fix must not make plain answers suspicious."""
    for reply in ("The sky is blue because air scatters short wavelengths more than long ones.",
                  "He is a polarising figure — brilliant at shipping, careless with people.",
                  "I'd go with something like Vidya Labs or Pragya Works."):
        assert not action_claims.promises_without_acting(reply)
