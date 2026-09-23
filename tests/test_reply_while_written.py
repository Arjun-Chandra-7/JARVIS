"""Asking the brain and speaking the answer at the same time.

No audio and no model: what is checked here is the join between them, which is where the
mistakes would be expensive and silent. Two in particular:

  - saying the answer twice, once as it streamed and again when the reply came back;
  - saying nothing at all, because a tool ran and the streamed text was never the answer.

Both look fine in isolation and only show up in the seam, which is why this test exists. The
whole contract is one flag: `handled` says the answer has already been spoken and shown, and the
caller does neither. False, and the caller does both — which is how a tool-calling turn, which
streams nothing, still gets answered out loud.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.audio import voice_session


class Recorder:
    """A session with the speaking replaced by a list."""

    def __init__(self, backend="local"):
        self.backend = backend
        self.spoken_after: list[str] = []
        self.streamed: list[str] = []
        self.events: list[tuple] = []

    # The two things _ask_and_say uses.
    def _speak(self, text, force=False):
        self.spoken_after.append(text)

    def _speak_as_written(self, pieces, on_sentence=None, force=False):
        # The real one splits fragments into sentences and reports each; a plain split on the
        # full stop is enough to exercise the reporting here.
        said = "".join(pieces)
        for sentence in [p for p in said.split(". ") if p]:
            if on_sentence is not None:
                on_sentence(sentence if sentence.endswith(".") else sentence + ".")
        self.streamed.append(said)
        return said

    def on_event(self, kind, text=None):
        self.events.append((kind, text))

    _ask_and_say = voice_session.VoiceSession._ask_and_say


class Brain:
    """A brain that emits fragments and then returns a reply."""

    def __init__(self, fragments, reply):
        self._fragments = fragments
        self._reply = reply
        self.on_reply_delta = None

    async def send(self, _prompt):
        for piece in self._fragments:
            if self.on_reply_delta is not None:
                self.on_reply_delta(piece)
            await asyncio.sleep(0)
        return self._reply


def run(fragments, reply, backend="local"):
    session = Recorder(backend)
    brain = Brain(fragments, reply)
    got, handled = asyncio.run(session._ask_and_say(brain, "anything"))
    return session, brain, got, handled


# --------------------------------------------------------------------------- the ordinary turn
def test_the_answer_is_spoken_as_it_arrives_and_not_again():
    """The failure this pins is hearing the whole reply twice."""
    session, _brain, got, handled = run(["Good morning", ", sir."], "Good morning, sir.")
    assert session.streamed == ["Good morning, sir."]
    assert handled is True                   # the caller must not say it again
    assert got == "Good morning, sir."


def test_the_finished_reply_is_what_comes_back():
    """Everything downstream — the HUD, the journal, the follow-up — wants the whole thing,
    and the speaking consumed it."""
    _session, _brain, got, handled = run(["One ", "two ", "three."], "One two three.")
    assert got == "One two three."


# ---------------------------------------------------------------------- when a tool ran instead
def test_an_answer_that_never_streamed_is_handed_back_to_be_spoken():
    """A turn that calls a tool speaks nothing while it runs, and the reply is assembled after.
    `handled` false is what tells the caller this one still has to be said out loud — left to
    the streaming alone, that answer would never be heard at all."""
    session, _brain, got, handled = run([], "I've opened Firefox, sir.")
    assert session.streamed == [""]
    assert handled is False
    assert got == "I've opened Firefox, sir."


def test_a_reply_rewritten_after_streaming_is_handed_back_too():
    """The model started talking, then called a tool, and the real answer is different. A
    half-sentence left hanging is worse than a repeated word, so the real one is said."""
    _session, _brain, got, handled = run(["One moment."], "I've opened Firefox, sir.")
    assert handled is False
    assert got == "I've opened Firefox, sir."


# --------------------------------------------------------------------------- housekeeping
def test_the_callback_is_removed_afterwards():
    """Left attached, the next turn's fragments would be pushed into a queue nobody drains."""
    _session, brain, _got, _handled = run(["Done."], "Done.")
    assert brain.on_reply_delta is None


def test_the_callback_is_removed_even_when_the_brain_fails():
    session = Recorder()

    class Broken:
        on_reply_delta = None

        async def send(self, _prompt):
            raise RuntimeError("brain died")

    brain = Broken()
    with pytest.raises(RuntimeError):
        asyncio.run(session._ask_and_say(brain, "anything"))
    assert brain.on_reply_delta is None


def test_the_hosted_voice_waits_for_the_whole_reply():
    """It is asked for one utterance at a time, so there is nothing to stream into."""
    session, brain, got, handled = run(["ignored"], "All done, sir.", backend="elevenlabs")
    assert session.streamed == []            # never started
    assert brain.on_reply_delta is None      # never attached
    assert handled is False                  # so the caller says it, exactly as before
    assert got == "All done, sir."


# ------------------------------------------------------------------------ what the screen shows
def test_each_sentence_reaches_the_screen_as_it_is_spoken():
    """Before this, the text was published only once the brain returned — so you heard the whole
    answer with nothing on screen, and then read it as Jarvis fell silent."""
    session, _brain, _got, handled = run(
        ["Good morning. ", "Your first meeting is at ten."],
        "Good morning. Your first meeting is at ten.")
    shown = [text for kind, text in session.events if kind == "reply"]
    assert shown == ["Good morning.", "Your first meeting is at ten."]
    assert handled is True          # so the caller does not print the whole thing underneath


def test_a_tool_turn_leaves_the_printing_to_the_caller():
    """Nothing streamed, so nothing was shown: the caller still has to."""
    session, _brain, _got, handled = run([], "I've opened Firefox, sir.")
    assert [t for k, t in session.events if k == "reply"] == []
    assert handled is False
