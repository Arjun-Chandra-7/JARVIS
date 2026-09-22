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
        if name == "kokoro_onnx":
            raise ImportError("no kokoro_onnx here")
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
    monkeypatch.setenv("JARVIS_KOKORO_DIR", str(tmp_path))
    assert k._weights_present() is False
    assert not list(tmp_path.iterdir()), "checking for the weights created something"


def test_the_weights_live_outside_the_system_disk(monkeypatch):
    """338 MB belongs in the cache this machine already keeps models in, not in /."""
    monkeypatch.delenv("JARVIS_KOKORO_DIR", raising=False)
    assert "Madara" in str(k.model_dir())


def test_a_british_voice_gets_british_phonemes():
    """The phonemiser's language has to agree with the voice or it mispronounces everything."""
    assert k._lang_for("bm_george") == "en-gb"
    assert k._lang_for("af_heart") == "en-us"


def test_a_long_answer_is_split_so_speech_can_start_early():
    """A four-sentence answer should start speaking after the first one, not after all four."""
    pieces = k._sentences("One thing. Then another thing. And a third. Finally a fourth.")
    assert len(pieces) == 4
    assert pieces[0] == "One thing."


def test_the_british_voice_is_the_default():
    """JARVIS is a particular voice, and the default should not be a coin flip among fifty-four."""
    assert k.DEFAULT_VOICE.startswith("b")


# --------------------------------------------------------------------------- starting to speak
class TestTimeToFirstWord:
    """Why the first piece is short.

    A reply is synthesised piece by piece and spoken as each piece lands, so the wait before
    Jarvis starts talking is the cost of the *first* piece alone. Measured on this machine: one
    five-second sentence took 1.67 s to synthesise, all of it silence. Breaking the opening
    clause off brought that to 1.05 s for the same line.
    """

    def test_a_long_opening_sentence_is_broken_at_a_clause(self):
        pieces = k._sentences(
            "Your first meeting is at ten with the design team, and the build from last night "
            "finished cleanly.")
        assert len(pieces) == 2
        assert pieces[0] == "Your first meeting is at ten with the design team,"
        assert pieces[1].startswith("and the build")

    def test_only_the_first_piece_is_cut_that_way(self):
        """A clause break mid-reply is audible as a stumble, and buys no latency by then."""
        pieces = k._sentences(
            "Right away. The deployment finished at nine, the tests all passed, and the site is "
            "live.")
        assert pieces[0] == "Right away."
        # The second sentence is long and full of commas, and is still left whole.
        assert pieces[1] == ("The deployment finished at nine, the tests all passed, and the "
                             "site is live.")

    def test_a_short_line_is_left_alone(self):
        assert k._sentences("On it, sir.") == ["On it, sir."]

    def test_a_sentence_with_nowhere_to_break_is_not_chopped_mid_phrase(self):
        """Better a slightly longer wait than a glitch in the middle of a word."""
        said = "Antidisestablishmentarianism " * 4
        assert k._sentences(said.strip()) == [said.strip()]

    def test_nothing_is_lost_or_duplicated_in_the_split(self):
        said = ("Good morning, sir. The overnight build finished, the backup completed, and "
                "there are two messages waiting for you.")
        assert " ".join(k._sentences(said)) == said


class TestWarmup:
    """The load happens at startup or it happens in front of the user."""

    def test_warmup_is_a_no_op_without_the_weights(self, monkeypatch):
        """It must never be the thing that downloads 338 MB, and never break startup."""
        monkeypatch.setattr(k, "available", lambda: False)
        called = []
        monkeypatch.setattr(k, "_get_pipeline", lambda v: called.append(v))
        assert k.warmup() is False
        assert called == []

    def test_a_voice_that_will_not_load_does_not_stop_startup(self, monkeypatch):
        monkeypatch.setattr(k, "available", lambda: True)

        def boom(_voice):
            raise RuntimeError("corrupt weights")

        monkeypatch.setattr(k, "_get_pipeline", boom)
        assert k.warmup() is False

    def test_the_voice_that_will_speak_is_the_one_warmed(self, monkeypatch):
        """Warming Piper on a Kokoro machine pays 1.6 s and leaves the real delay unpaid."""
        from jarvis.audio import local_tts

        monkeypatch.setattr(local_tts, "_better_voice_available", lambda: True)
        monkeypatch.setattr(k, "available", lambda: True)
        loaded = []
        monkeypatch.setattr(k, "_get_pipeline", lambda v: loaded.append(v))
        monkeypatch.setattr(local_tts, "_get_voice",
                            lambda _p: pytest.fail("Piper was warmed instead of Kokoro"))
        assert local_tts.warmup("/nonexistent.onnx") is True
        assert loaded == [k.DEFAULT_VOICE]

    def test_piper_is_warmed_when_kokoro_would_not_load(self, monkeypatch):
        from jarvis.audio import local_tts

        monkeypatch.setattr(local_tts, "_better_voice_available", lambda: True)
        monkeypatch.setattr(k, "warmup", lambda *_a, **_k: False)
        monkeypatch.setattr(local_tts, "_get_voice", lambda _p: object())
        assert local_tts.warmup("/nonexistent.onnx") is True
