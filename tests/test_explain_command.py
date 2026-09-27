"""Topic questions are taught by the strong model, not answered in one line by the local brain."""
import asyncio

import pytest

from jarvis import explain_command as ex


@pytest.fixture
def tutor(monkeypatch):
    from jarvis.llm import Completion
    seen = {"prompts": [], "ok": True}

    async def fake_complete(system, prompt, config=None, temperature=0.2, timeout=60.0, strength="default"):
        seen["prompts"].append(prompt)
        seen["system"], seen["strength"] = system, strength
        if not seen["ok"]:
            return Completion(text="", failures=["groq: rate_limited"])
        return Completion(text="An RNN reads a sequence one step at a time…", provider="groq:test", quality="strong")

    monkeypatch.setattr("jarvis.llm.complete_detailed", fake_complete)
    monkeypatch.setattr("jarvis.context._CURRENT", "voice")
    monkeypatch.setattr("jarvis.power.asleep", lambda: False)
    ex._LAST.clear()
    return seen


@pytest.mark.parametrize("said", [
    "What is a sequential input or sequential output in an RNN?",   # heard live
    "explain quantum physics", "what is the pythagoras theorem", "why is the sky blue",
    "how does a transformer work", "tell me about black holes", "photosynthesis kya hota hai",
    "ओम का नियम क्या है", "So what are prime numbers?",
])
def test_topics(said):
    assert ex.topic(said)


@pytest.mark.parametrize("said", [
    "what is the time", "what is my schedule", "what is the status of the Claude terminal?",
    "how do I open settings", "explain this", "what is study mode", "what is the weather today",
    "whats up", "what is going on", "what is the latest version of python", "what is this theorem called",
])
def test_not_topics(said):
    assert ex.topic(said) is None


def test_a_topic_is_taught_by_the_strong_model_through_the_router(tutor):
    from jarvis import commands
    from jarvis.config import CONFIG
    reply = asyncio.run(commands.handle("What is a sequential input or sequential output in an RNN?", CONFIG, "voice"))
    assert reply.startswith("An RNN reads") and tutor["strength"] == "strong"
    assert "concrete example" in tutor["system"] and "Answer in English." in tutor["prompts"][-1]


def test_a_follow_up_continues_the_same_topic(tutor):
    asyncio.run(ex.handle("What is a sequential input in an RNN?"))
    asyncio.run(ex.handle("So give me some examples"))
    p = tutor["prompts"][-1]
    assert "Earlier they asked: \"What is a sequential input in an RNN?\"" in p and "give me some examples" in p


def test_a_follow_up_with_no_topic_is_not_ours(tutor):
    assert asyncio.run(ex.handle("give me some examples")) is None


def test_hinglish_question_gets_hinglish(tutor):
    asyncio.run(ex.handle("photosynthesis kya hota hai"))
    assert "Hinglish (Roman script)" in tutor["prompts"][-1]


def test_no_strong_model_lets_the_brain_answer(tutor):
    tutor["ok"] = False
    assert asyncio.run(ex.handle("explain quantum physics")) is None
