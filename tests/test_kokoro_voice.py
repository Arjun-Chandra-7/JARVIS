"""Choosing how a line is read, and refusing to promise a voice we cannot produce.

The delivery layer is deterministic and runs before any model sees the text, so it is testable
without the weights being on disk — which is the point of testing it: this is the part that must
still behave on a machine where Kokoro was never installed.
"""

from __future__ import annotations

import pytest

from jarvis.audio import kokoro_tts as k


@pytest.mark.parametrize("line", [
    "Good morning, sir.",
    "Good evening.",
    "Welcome back, sir.",
    "Goodnight, sir.",
])
def test_greetings_are_read_warmly(line):
    """The two lines a day that are not work."""
    assert k.delivery_for(line) is k.WARM


@pytest.mark.parametrize("line", [
    "On it, sir.",
    "Right away.",
    "Of course, sir.",
    "Understood.",
])
def test_acknowledgements_are_brisk(line):
    """"On it, sir" should not be savoured."""
    assert k.delivery_for(line) is k.BRISK


@pytest.mark.parametrize("line", [
    "The backup failed, sir.",
    "I could not reach the calendar.",
    "Opera GX is offline.",
    "That request timed out.",
    "Permission was denied.",
])
def test_trouble_is_read_slowly(line):
    assert k.delivery_for(line) is k.GRAVE


def test_trouble_wins_over_a_greeting_that_opens_the_same_line():
    """"Good morning. The backup failed." is a failure, whatever it opens with — so the check
    for trouble has to come first, and this is the test that keeps it there."""
    assert k.delivery_for("Good morning, sir. The overnight backup failed.") is k.GRAVE


@pytest.mark.parametrize("line", [
    "It is nineteen degrees and clear.",
    "You have three meetings today, the first at ten.",
    "",
])
def test_everything_else_is_neutral(line):
    assert k.delivery_for(line) is k.NEUTRAL


def test_the_deliveries_are_ordered_the_way_they_sound():
    """Brisk is faster than neutral is faster than grave. If that ever inverts, every line is
    read wrong and nothing else here would catch it."""
    assert k.BRISK.speed > k.NEUTRAL.speed > k.WARM.speed > k.GRAVE.speed


def test_every_delivery_says_why_it_exists():
    """A tone with no stated reason is a tone nobody can judge the next one against."""
    for delivery in k.DELIVERIES.values():
        assert delivery.why, f"{delivery.name} has no reason attached"


def test_there_are_few_enough_of_them_to_remember():
    """Twenty shades would be a settings screen nobody tunes, and a model asked to pick between
    differences this model cannot actually produce."""
    assert len(k.DELIVERIES) == 4


# --------------------------------------------------------------------- refusing to over-promise
def test_without_the_package_it_is_unavailable_and_says_what_to_do(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_kokoro(name, *args, **kwargs):
        if name == "kokoro":
            raise ImportError("no kokoro here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_kokoro)
    assert k.available() is False
    assert "pip install" in k.how_to_install()


def test_without_the_weights_it_is_unavailable_even_with_the_package(monkeypatch):
    """330 MB is not something to download because somebody said good morning."""
    monkeypatch.setattr(k, "_weights_present", lambda: False)
    assert k.available() is False


def test_checking_for_weights_never_downloads_them(monkeypatch, tmp_path):
    """_weights_present looks at the disk and nothing else. If it ever reached for the network
    this would be the test that noticed."""
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert k._weights_present() is False
    assert not list(tmp_path.iterdir()), "checking for the weights created something"


def test_the_british_voice_is_the_default():
    """JARVIS is a particular voice, and the default should not be a coin flip among fifty-four."""
    assert k.DEFAULT_VOICE.startswith("b")
