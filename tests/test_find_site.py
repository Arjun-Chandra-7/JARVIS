"""Finding a site means opening it and checking, not believing its title."""
import pytest

from jarvis import find_site


@pytest.mark.parametrize("said,kind,subject", [
    # the request that prompted this, typed exactly as it was said
    ("find a online site where u can get a whiteboard for free, and draw me the mona lisa",
     "whiteboard", "mona lisa"),
    ("find a free whiteboard and draw the eiffel tower", "whiteboard", "eiffel tower"),
    ("open a whiteboard and sketch me a cat", "whiteboard", "cat"),
    ("find me a site with a canvas and then paint a picture of an owl", "canvas", "owl"),
])
def test_one_sentence_asking_for_both(said, kind, subject):
    assert find_site.parse_find_and_draw(said) == (kind, subject)


@pytest.mark.parametrize("said", [
    "find a free online whiteboard site",   # a site, but nothing to draw
    "draw me the mona lisa",                # something to draw, but no site to find
    "open netflix",
    "find my keys",
])
def test_not_a_combined_request(said):
    assert find_site.parse_find_and_draw(said) is None


@pytest.mark.parametrize("said,purpose", [
    ("find a free online whiteboard site", "whiteboard"),
    ("find me a site where i can get a whiteboard", "whiteboard"),
    ("find a good site for recipes", "recipes"),
])
def test_what_kind_of_site_is_wanted(said, purpose):
    assert find_site.parse(said) == purpose


@pytest.mark.parametrize("said", ["find my keys", "open netflix", "what is the volume"])
def test_not_a_request_for_a_site(said):
    """"find my keys" is not a request for a website."""
    assert find_site.parse(said) is None


def test_a_drawing_site_is_checked_for_a_drawing_surface():
    """The check is the whole point: a title says what a page claims to be."""
    assert find_site.check_for("a free whiteboard") is find_site._has_canvas
    assert find_site.check_for("somewhere to draw") is find_site._has_canvas
    assert find_site.check_for("recipes") is find_site._loads_at_all


def test_a_favicon_is_not_a_drawing_surface():
    """Pages are full of small canvases — sparklines, avatars, tracking pixels."""
    import asyncio

    from jarvis.integrations import browser

    async def tiny():
        return {"x": 0, "y": 0, "w": 16, "h": 16, "tag": "canvas"}

    original = browser.canvas_box
    try:
        browser.canvas_box = tiny
        assert asyncio.run(find_site._has_canvas()) is False
    finally:
        browser.canvas_box = original
