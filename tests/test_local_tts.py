"""Speech chunking: Jarvis must start talking on the first sentence, without mangling the text."""
import pytest

from jarvis.audio import local_tts as t


def test_empty_text_yields_no_chunks():
    assert t.split_for_speech("") == []
    assert t.split_for_speech("   ") == []


def test_single_sentence_is_one_chunk():
    assert t.split_for_speech("Your battery is at sixty two percent.") == [
        "Your battery is at sixty two percent."
    ]


def test_splits_on_sentence_boundaries_keeping_punctuation():
    out = t.split_for_speech("Battery is fine. Weather is clear. You have one meeting.")
    assert out == ["Battery is fine.", "Weather is clear.", "You have one meeting."]


def test_question_and_exclamation_end_sentences():
    assert t.split_for_speech("Ready? Let's go! Now.") == ["Ready?", "Let's go!", "Now."]


def test_long_sentence_is_split_on_clauses_so_first_audio_is_quick():
    long = (
        "I checked your calendar and your mail and your messages, "
        "then I opened the report you asked about, "
        "and I also found two things that need a reply before this evening, "
        "including one from your bank that looks time sensitive."
    )
    out = t.split_for_speech(long, max_chars=80)
    assert len(out) > 1
    assert all(len(c) <= 80 for c in out), [len(c) for c in out]


def test_no_text_is_lost_when_splitting():
    text = "First part here. Second part, with a clause, continues on. Third."
    joined = " ".join(t.split_for_speech(text, max_chars=30))
    # Same words, same order — only whitespace may differ.
    assert joined.split() == text.split()


def test_oversized_run_on_without_punctuation_is_hard_wrapped():
    text = "word " * 100
    out = t.split_for_speech(text, max_chars=60)
    assert out, "a run-on with no punctuation produced nothing"
    assert all(len(c) <= 60 for c in out)
    assert " ".join(out).split() == text.split()


def test_whitespace_and_newlines_do_not_create_empty_chunks():
    out = t.split_for_speech("One.   \n\n  Two.  ")
    assert out == ["One.", "Two."]


@pytest.mark.parametrize("bad", [None, 0, []])
def test_falsy_input_is_safe(bad):
    assert t.split_for_speech(bad or "") == []


def test_kokoro_failure_after_audio_does_not_repeat_the_reply_in_piper(monkeypatch):
    from jarvis.audio import kokoro_tts

    monkeypatch.setattr(t, "_better_voice_available", lambda: True)

    def interrupted(*_args, **_kwargs):
        yield b"first sentence", 24000
        raise RuntimeError("synthesizer failed")

    monkeypatch.setattr(kokoro_tts, "synth_stream", interrupted)
    monkeypatch.setattr(t, "_get_voice", lambda *_: (_ for _ in ()).throw(
        AssertionError("Piper replayed the whole response")))
    assert list(t.synth_stream("First sentence. Second sentence.", "unused")) == [
        (b"first sentence", 24000)]
