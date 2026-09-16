"""Finding words on screen is a question about text, not a question for a vision model."""
import pytest

from jarvis.vision import ocr


def _word(text, x=100, y=50, conf=0.9):
    return ocr.Word(text=text, x=x, y=y, left=x - 20, top=y - 8,
                    right=x + 20, bottom=y + 8, confidence=conf)


@pytest.mark.parametrize("target,seen", [
    # OCR runs words together; spacing cannot be part of the match
    ("New chat", "米Newchat-Claude"),
    ("whiteboard", "OnlineWhiteboard.org"),
    ("Free Forever", "100%Free,Forever"),
    ("about", "OABOUT"),
    ("allow", "Allow cookies"),
])
def test_a_label_is_found_inside_the_text_around_it(target, seen):
    """Scoring containment by length ratio put every one of these below a coin toss, and nothing
    on screen was ever found."""
    assert ocr.find(target, [_word(seen)]) is not None


@pytest.mark.parametrize("target", ["nonexistent thing", "zzzzqqq", "xylophone"])
def test_something_that_is_not_there_is_not_invented(target):
    assert ocr.find(target, [_word("OnlineWhiteboard.org"), _word("Settings")]) is None


def test_the_closest_match_wins():
    words = [_word("Cancel", x=100), _word("Save", x=300), _word("Save as…", x=500)]
    assert ocr.find("save", words).x == 300


def test_an_empty_screen_finds_nothing():
    assert ocr.find("anything", []) is None
    assert ocr.find("", [_word("something")]) is None


def test_low_confidence_loses_to_a_clear_reading():
    words = [_word("Submit", x=100, conf=0.30), _word("Submit", x=400, conf=0.95)]
    assert ocr.find("submit", words).x == 400


def test_positions_come_back_in_screen_pixels():
    """The screenshot may be scaled; a click needs real coordinates."""
    word = _word("Allow", x=640, y=360)
    found = ocr.find("allow", [word])
    assert (found.x, found.y) == (640, 360)
    assert found.box == (620, 352, 660, 368)
