"""Command words as they actually arrive from a real microphone.

Every phrase here was transcribed from real speech in this house and is in the log.
"""
import pytest

from jarvis.misheard import fix


@pytest.mark.parametrize("heard,contains", [
    ("Find a site where you can access the wide-board and runnyam onarisa", "whiteboard"),
    ("Find the free vibe both side and draw them", "whiteboard"),
    ("open a white board", "whiteboard"),
    ("Drone the Mona Lisa here", "draw"),
    ("jarvis dick tate", "dictate"),
    ("start dig tation", "dictation"),
])
def test_a_misheard_command_word_is_put_right(heard, contains):
    assert contains in fix(heard).lower()


def test_the_whole_mangled_request_becomes_parseable():
    """This exact sentence reached Jarvis and matched nothing."""
    from jarvis.commands import clean_text
    from jarvis.find_site import parse_find_and_draw

    heard = "Find a site where you can access the wide-board and runnyam onarisa"
    assert parse_find_and_draw(fix(clean_text(heard))) == ("whiteboard", "mona lisa")


@pytest.mark.parametrize("said", [
    "open netflix and turn the volume up",
    "what is the volume",
    "set the brightness to 40",
    "how are you doing",
])
def test_clean_speech_is_not_touched(said):
    assert fix(said) == said


def test_a_wide_screen_is_not_a_whiteboard():
    """The fixes must not rewrite words that were heard correctly."""
    assert "whiteboard" not in fix("open the wide screen view").lower()
