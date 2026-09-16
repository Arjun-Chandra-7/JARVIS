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
    # The working size, not the file's: strokes are produced in it and scaled_into fits from it.
    assert plan.source_size[0] > plan.source_size[1]      # landscape in, landscape out
    assert min(plan.source_size) == strokes.WORKING_SIZE


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
    """"Draw it" with nothing to point at is still not a request — the context is cleared here
    because it is global, and a picture left behind by another test would give "it" a meaning."""
    from jarvis import context
    context.forget("local")
    assert draw_command.parse(said) is None


def test_draw_it_means_the_picture_that_was_just_made():
    """The other half of the rule above: once Jarvis has generated a picture, "it" is that."""
    from jarvis import context
    context.forget("local")
    assert draw_command.parse("draw it on a whiteboard") is None
    context.note_picture("/tmp/whatever.png")
    try:
        assert draw_command.parse("draw it on a whiteboard") == "it"
        assert draw_command.parse("draw this") == "this"
    finally:
        context.forget("local")


def test_tone_is_what_makes_it_a_picture(tmp_path):
    """A gradient has no edges at all. Edge detection finds nothing in it; hatching renders it,
    which is the difference between a drawing that reads as a face and one that does not."""
    import numpy as np
    from PIL import Image

    ramp = np.tile(np.linspace(0, 255, 400, dtype=np.uint8), (300, 1))
    path = tmp_path / "ramp.png"
    Image.fromarray(ramp).save(path)

    plan = strokes.from_image(path)
    assert plan is not None and len(plan.strokes) > 50

    # More strokes over the dark half than the light half: that is what tone means here.
    midpoint = plan.source_size[0] / 2
    dark = sum(1 for s in plan.strokes if s[0][0] < midpoint)
    light = sum(1 for s in plan.strokes if s[0][0] >= midpoint)
    assert dark > light * 2


def test_a_hatch_line_is_two_points(tmp_path):
    """Requiring four discarded every hatch stroke, and the tone never appeared."""
    import numpy as np
    from PIL import Image

    # A dark disc on a light ground: real tone, so there is something to hatch.
    field = np.full((300, 300), 230, np.uint8)
    y, x = np.ogrid[:300, :300]
    field[(x - 150) ** 2 + (y - 150) ** 2 < 90 ** 2] = 30
    Image.fromarray(field).save(tmp_path / "disc.png")

    plan = strokes.from_image(tmp_path / "disc.png")
    assert plan is not None
    assert any(len(s) == 2 for s in plan.strokes)


def test_a_flat_field_has_nothing_to_draw(tmp_path):
    """No edges and no tone: saying so beats inventing strokes."""
    import numpy as np
    from PIL import Image

    Image.fromarray(np.full((300, 300), 30, np.uint8)).save(tmp_path / "flat.png")
    assert strokes.from_image(tmp_path / "flat.png") is None


def test_a_dark_feature_inside_a_bright_region_is_drawn(tmp_path):
    """The case that was being lost: an eye on a lit face. Before local equalisation the whole
    face sat above every global threshold, so Vermeer's girl, the figure in The Scream and
    Einstein all came out as white cutouts with nothing but an outline."""
    import numpy as np
    from PIL import Image

    field = np.full((400, 400), 120, np.uint8)      # mid ground
    y, x = np.ogrid[:400, :400]
    face = (x - 200) ** 2 / 110 ** 2 + (y - 200) ** 2 / 140 ** 2 < 1
    field[face] = 225                                # a bright face
    for eye_x in (165, 235):                         # two dark features on it
        field[((x - eye_x) ** 2 + (y - 180) ** 2) < 16 ** 2] = 70
    path = tmp_path / "face.png"
    Image.fromarray(field).save(path)

    plan = strokes.from_image(path)
    assert plan is not None

    w, h = plan.source_size
    near_eyes = 0
    for stroke in plan.strokes:
        for cx, cy in stroke:
            if 0.35 * h < cy < 0.55 * h and 0.35 * w < cx < 0.65 * w:
                near_eyes += 1
                break
    assert near_eyes > 5, f"only {near_eyes} strokes reached the features"
