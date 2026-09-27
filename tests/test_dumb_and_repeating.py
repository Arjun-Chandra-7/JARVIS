"""From the history on 24 Sep, 21:57–22:06: Jarvis answering its own voice, greeting on every
turn, a 3B model answering everything, and "explain the poem on my screen" never drawing."""
from __future__ import annotations

import time

import pytest

from jarvis.audio import speech_text, voice_session
from jarvis.teach import intents
from test_voice_conversation_flow import Brain, make_session, run_conversation


# --------------------------------------------------------------------------- its own voice
def test_its_own_words_heard_back_are_ignored(monkeypatch):
    s = make_session([], monkeypatch)
    s._note_said("Give me a moment.")
    s._note_said("I can read from the screen or help with other tasks. What would you like to do next?")
    assert s._is_own_echo("Give me a moment.")
    assert s._is_own_echo("What would you like?")
    assert not s._is_own_echo("Clear.")                         # the person's one-word command
    assert not s._is_own_echo("explain the poem on my screen")
    s._recently_said = [(time.monotonic() - 60, w) for _, w in s._recently_said]
    assert not s._is_own_echo("Give me a moment.")              # long ago: could be the person


def test_an_echo_in_the_conversation_is_not_answered(monkeypatch):
    s = make_session([None], monkeypatch)
    s._note_said("Give me a moment.")
    brain = Brain()
    run_conversation(s, brain, "Give me a moment.")
    assert brain.asked == []


def test_the_name_alone_gets_yes_not_a_greeting(monkeypatch):
    s = make_session([None], monkeypatch)
    brain = Brain()
    run_conversation(s, brain, "Hey, Javis.")
    assert brain.asked == [] and s.spoken[0] == "Yes?"


# --------------------------------------------------------------------------- greetings and offers
@pytest.mark.parametrize("line", ["Hello.", "Good evening.", "Evening.", "How can I assist you today?",
                                  "What can I assist you with now?", "What would you like to do next?",
                                  "Is there a particular task or information you need help with tonight?",
                                  "Feel free to call if you need anything.", "I'm here."])
def test_filler_is_never_said(line):
    assert speech_text.is_filler(line)


@pytest.mark.parametrize("line", ["Hello is the first word of the song.", "The evening news starts at nine.",
                                  "What would you like me to send to Papa?"])
def test_real_sentences_are_kept(line):
    assert not speech_text.is_filler(line)


def test_speakable_drops_filler_but_keeps_the_answer():
    out = voice_session.speakable("Your CPU is at 39 percent. What can I help with?")
    assert out == "Your CPU is at 39 percent."
    assert voice_session.speakable("Yes?") == "Yes?"


def test_startup_greeting_once_per_login():
    src = (voice_session.__file__)
    text = open(src, encoding="utf-8").read()
    assert "jarvis-greeted" in text and "if not marker.exists():" in text


# --------------------------------------------------------------------------- the brain
def test_ollama_brain_means_open_models_when_groq_is_there(monkeypatch):
    from jarvis.config import Config
    monkeypatch.setenv("JARVIS_BRAIN", "ollama")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("JARVIS_BRAIN_LOCAL_ONLY", raising=False)
    c = Config()
    assert c.brain == "groq" and "gpt-oss" in c.groq_model
    monkeypatch.setenv("JARVIS_BRAIN_LOCAL_ONLY", "1")
    assert Config().brain == "ollama"


def test_strong_models_are_open_weight_first(monkeypatch):
    from jarvis import providers
    from jarvis.config import Config
    monkeypatch.setenv("GROQ_API_KEY", "k")
    names = [p.model for p in providers.configured(Config())]
    assert names[0] == "openai/gpt-oss-120b" and "openai/gpt-oss-20b" in names
    assert names[-1] == Config().ollama_model                   # local stays the last resort


# --------------------------------------------------------------------------- "explain the poem on my screen"
@pytest.mark.parametrize("heard", ["Explain the poem on the screen using a diagram.", "I explained the poem on my screen.",
                                   "Jarvis explained the part of the poem on my screen.", "Explain me the poem on my screen.",
                                   "On my screen explain me this part of the chapter"])
def test_every_way_it_was_said_draws_the_screen(heard):
    i = intents.lesson(heard)
    assert i and i.name == "screen"
