"""Turning a stream of model fragments back into sentences, early.

A reply is spoken only once it has been written in full, so the wait before Jarvis says anything
includes the whole generation. For a three-sentence answer that is most of the wait, and none of
it is visible — there is nothing on screen to explain the pause.

A model emits fragments, not sentences: "Your first", " meeting is at", " ten." What is tested
here is the piece that turns those back into sentences and releases each one the moment it is
complete, so speech can start after the first sentence is written rather than after the last.

No audio is involved. The failures that matter are all about where the cuts land, and a cut in
the wrong place is audible as a stumble whatever synthesises it.
"""

from __future__ import annotations

from jarvis.audio.local_tts import sentences_as_they_arrive as split


def test_a_sentence_is_released_as_soon_as_it_is_finished():
    """The whole point: the first sentence does not wait for the second."""
    out = list(split(["Good morning.", " Your first meeting is at ten."]))
    assert out == ["Good morning.", "Your first meeting is at ten."]


def test_fragments_that_break_mid_word_are_reassembled():
    """Models split wherever the tokeniser did, not where the words are."""
    out = list(split(["Your fir", "st meet", "ing is at ten."]))
    assert out == ["Your first meeting is at ten."]


def test_the_opening_is_cut_on_a_clause_when_it_runs_long():
    """Same reason kokoro cuts it: nothing is heard until the first piece is synthesised, so the
    first piece should be short."""
    out = list(split(["Your first meeting is at ten with the design team,",
                      " and the build from last night finished cleanly."]))
    assert out[0] == "Your first meeting is at ten with the design team,"
    assert out[1].startswith("and the build")


def test_only_the_opening_is_cut_that_way():
    """A clause break later in a reply is a stumble, and buys no latency by then."""
    out = list(split(["Right away.",
                      " The deployment finished at nine, the tests all passed, and it is live."]))
    assert out[0] == "Right away."
    assert out[1] == ("The deployment finished at nine, the tests all passed, and it is live.")


def test_a_short_opening_is_not_cut_at_all():
    assert list(split(["On it, sir."])) == ["On it, sir."]


def test_an_abbreviation_does_not_end_a_sentence():
    """Speaking "Dr." and then "Smith is waiting" puts a breath in the middle of a name."""
    out = list(split(["Dr. Smith is waiting.", " Shall I let him in?"]))
    assert out == ["Dr. Smith is waiting.", "Shall I let him in?"]


def test_an_initial_does_not_end_a_sentence():
    out = list(split(["A. Sharma called about the invoice."]))
    assert out == ["A. Sharma called about the invoice."]


def test_a_question_and_an_exclamation_both_end_one():
    out = list(split(["Is that all? ", "Very good! ", "I'll get on with it."]))
    assert out == ["Is that all?", "Very good!", "I'll get on with it."]


def test_an_unfinished_tail_is_still_spoken():
    """A model that stops mid-sentence still said something, and silence would be the worse
    failure — the user would hear the answer cut off with no sign anything went wrong."""
    assert list(split(["The build failed because"])) == ["The build failed because"]


def test_nothing_is_lost_across_the_whole_stream():
    deltas = ["Good morning, sir.", " The overnight build finished,",
              " the backup completed,", " and there are two messages for you."]
    assert " ".join(split(deltas)) == "".join(deltas).strip()


def test_empty_fragments_are_ignored():
    """Streams contain keep-alive and role-only chunks with no text in them."""
    assert list(split(["", "Done.", "", ""])) == ["Done."]


def test_an_empty_stream_says_nothing():
    assert list(split([])) == []
    assert list(split(["", ""])) == []
