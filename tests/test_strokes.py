"""A picture becomes lines a pointer can draw, within a budget that keeps it watchable."""
import pytest

from jarvis import draw_command
from jarvis.vision import strokes


@pytest.fixture
def line_art(tmp_path):
    """A shape with real contours, so the geometry is known rather than guessed at."""
    from PIL import Image, ImageDraw

    path = tmp_path / "shape.png"
    im = Image.new("L", (400, 300), 255)
    d = ImageDraw.Draw(im)
    d.ellipse((60, 40, 340, 260), outline=0, width=5)
    d.line((60, 150, 340, 150), fill=0, width=5)
    im.save(path)
    return path


def test_a_picture_becomes_strokes(line_art):
    plan = strokes.from_image(line_art)
    assert plan is not None
    assert plan.strokes and plan.points > 0
    assert plan.source_size == (400, 300)


def test_the_point_budget_is_respected(line_art):
    """Every point is one dispatched mouse event, so this is really a time budget."""
    plan = strokes.from_image(line_art, budget=60)
    assert plan.points <= 60


def test_blank_paper_yields_nothing_rather_than_noise(tmp_path):
    from PIL import Image

    blank = tmp_path / "blank.png"
    Image.new("L", (300, 300), 255).save(blank)
    assert strokes.from_image(blank) is None


def test_a_missing_file_is_not_an_exception(tmp_path):
    assert strokes.from_image(tmp_path / "nope.png") is None


def test_strokes_are_fitted_inside_the_box(line_art):
    plan = strokes.from_image(line_art)
    fitted = plan.scaled_into(100, 50, 600, 400)
    xs = [x for stroke in fitted for x, _ in stroke]
    ys = [y for stroke in fitted for _, y in stroke]
    assert min(xs) >= 100 and max(xs) <= 700
    assert min(ys) >= 50 and max(ys) <= 450


def test_the_aspect_ratio_survives_being_fitted(line_art):
    """A portrait squeezed into a landscape box stops looking like the thing it came from."""
    plan = strokes.from_image(line_art)
    fitted = plan.scaled_into(0, 0, 1200, 300)
    xs = [x for s in fitted for x, _ in s]
    ys = [y for s in fitted for _, y in s]
    drawn_ratio = (max(xs) - min(xs)) / max(1, (max(ys) - min(ys)))
    source_ratio = plan.source_size[0] / plan.source_size[1]
    assert abs(drawn_ratio - source_ratio) < 0.25


# ------------------------------------------------------------------ the spoken request
@pytest.mark.parametrize("said,subject", [
    ("draw me the mona lisa", "mona lisa"),
    ("draw a cat on the whiteboard", "cat"),
    ("sketch the eiffel tower", "eiffel tower"),
    ("draw me a picture of a dog", "dog"),       # searching the whole phrase finds picture frames
    ("paint an owl", "owl"),
])
def test_what_is_being_asked_for(said, subject):
    assert draw_command.parse(said) == subject


@pytest.mark.parametrize("said", ["draw it", "draw", "open netflix", "what is the volume"])
def test_not_a_drawing_request(said):
    assert draw_command.parse(said) is None
