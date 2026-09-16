"""Reading the screen is a question about text; describing it is a question about meaning."""
import pytest

from jarvis import screen_read_command as src


@pytest.mark.parametrize("said", [
    "what does my screen say",
    "what does it say",
    "read my screen",
    "read out the screen",
    "whats on my screen",
    "what is on the screen",
    "screen text",
])
def test_these_ask_for_the_words(said):
    assert src.wants_the_words(said)


@pytest.mark.parametrize("said", [
    "what am I looking at",
    "describe my screen",
    "look at my screen",
])
def test_these_ask_what_it_means(said):
    """Slower and paraphrased, which is the right trade only when the answer is not already
    written on the screen."""
    assert src.wants_a_description(said)
    assert not src.wants_the_words(said)


@pytest.mark.parametrize("said", [
    "open netflix", "what is the volume", "click allow on my screen",
    "draw me the mona lisa", "",
])
def test_neither(said):
    assert not src.wants_the_words(said)
    assert not src.wants_a_description(said)


def test_an_unreadable_screen_says_so(monkeypatch):
    import asyncio

    from jarvis.vision import ocr

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read", lambda *a, **k: [])
    said = asyncio.run(src.handle("what does my screen say"))
    assert "can't read any text" in said
