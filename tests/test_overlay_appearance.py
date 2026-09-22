"""The two ways the overlay can come up looking wrong.

Neither is catchable by running it here: the first only shows on a compositor that refuses a
transparent visual, and the second only shows to someone reading the screen. Both were reported
as the same sentence — "the jarvis is opening black for me" — so both are pinned by reading the
files, which is the one thing a test on this machine can honestly do.

What was verified by looking rather than by reading: the pill renders light, captured from a
running build of this branch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

OVERLAY = Path(__file__).resolve().parent.parent / "overlay"


@pytest.fixture(scope="module")
def main_js() -> str:
    return (OVERLAY / "main.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tokens_css() -> str:
    return (OVERLAY / "tokens.css").read_text(encoding="utf-8")


def test_the_window_does_not_fall_back_to_black(main_js):
    """`transparent: true` is a request, not a guarantee.

    A compositor that will not grant a transparent visual drops the alpha and paints the rest of
    backgroundColor. When that was #00000000 the rest was black, so the overlay arrived as a
    black rectangle — which is exactly how this was reported.
    """
    found = re.search(r"backgroundColor:\s*\"(#[0-9a-fA-F]{8})\"", main_js)
    assert found, "the window must state a backgroundColor, so the fallback is a decision"
    colour = found.group(1).lower()
    assert colour[1:3] == "00", "it must still be fully transparent when transparency works"
    rgb = colour[3:]
    assert rgb != "000000", "a black fallback is the bug; fail into the design instead"
    # Light, so the fallback is the surface the design already uses.
    assert all(int(rgb[i:i + 2], 16) >= 0xE0 for i in (0, 2, 4)), rgb


def test_light_is_the_default_and_dark_is_opt_in(tokens_css):
    """Dark must live behind an attribute. Defined the other way round, a machine that reports
    no preference gets the dark theme — and the report was that it comes up black."""
    base = tokens_css.index(":root {")
    dark = tokens_css.index('body[data-appearance="dark"]')
    assert base < dark, "the light palette must be the one defined on bare :root"

    # The default surface the pill is drawn on has to be light.
    root_block = tokens_css[base:dark]
    surface = re.search(r"--surface-base:\s*(#[0-9a-fA-F]{6})", root_block)
    assert surface, "--surface-base must be defined in the default palette"
    value = surface.group(1)[1:]
    assert all(int(value[i:i + 2], 16) >= 0xE0 for i in (0, 2, 4)), value


def test_the_pill_is_nearly_opaque(tokens_css):
    """Translucent enough to sit on a desktop, solid enough to read a sentence through."""
    alpha = re.search(r"--pill-alpha:\s*([0-9.]+)", tokens_css)
    assert alpha, "--pill-alpha must be defined once, in the tokens"
    assert 0.85 <= float(alpha.group(1)) <= 1.0
