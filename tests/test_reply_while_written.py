"""Asking the brain and speaking the answer at the same time.

No audio and no model: what is checked here is the join between them, which is where the
mistakes would be expensive and silent. Two in particular:

  - saying the answer twice, once as it streamed and again when the reply came back;
  - saying nothing at all, because a tool ran and the streamed text was never the answer.

Both look fine in isolation and only show up in the seam, which is why this test exists.
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

    # The two things _ask_and_say uses.
    def _speak(self, text, force=False):
        self.spoken_after.append(text)

    def _speak_as_written(self, pieces, force=False):
        said = "".join(pieces)
        self.streamed.append(said)
        return said

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
    got = asyncio.run(session._ask_and_say(brain, "anything"))
    return session, brain, got


# --------------------------------------------------------------------------- the ordinary turn
def test_the_answer_is_spoken_as_it_arrives_and_not_again():
    """The failure this pins is hearing the whole reply twice."""
    session, _brain, got = run(["Good morning", ", sir."], "Good morning, sir.")
    assert session.streamed == ["Good morning, sir."]
    assert session.spoken_after == []        # nothing said a second time
    assert got == "Good morning, sir."


def test_the_finished_reply_is_what_comes_back():
    """Everything downstream — the HUD, the journal, the follow-up — wants the whole thing,
    and the speaking consumed it."""
    _session, _brain, got = run(["One ", "two ", "three."], "One two three.")
    assert got == "One two three."


# ---------------------------------------------------------------------- when a tool ran instead
def test_an_answer_that_never_streamed_is_still_spoken():
    """A turn that calls a tool speaks nothing while it runs, and the reply is assembled after.
    Left to the streaming alone, that answer would never be said out loud."""
    session, _brain, got = run([], "I've opened Firefox, sir.")
    assert session.streamed == [""]
    assert session.spoken_after == ["I've opened Firefox, sir."]
    assert got == "I've opened Firefox, sir."


def test_a_reply_rewritten_after_streaming_is_said_properly():
    """The model started talking, then called a tool, and the real answer is different. A
    half-sentence left hanging is worse than a repeated word."""
    session, _brain, _got = run(["One moment."], "I've opened Firefox, sir.")
    assert session.spoken_after == ["I've opened Firefox, sir."]


# --------------------------------------------------------------------------- housekeeping
def test_the_callback_is_removed_afterwards():
    """Left attached, the next turn's fragments would be pushed into a queue nobody drains."""
    _session, brain, _got = run(["Done."], "Done.")
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
    session, brain, got = run(["ignored"], "All done, sir.", backend="elevenlabs")
    assert session.streamed == []            # never started
    assert brain.on_reply_delta is None      # never attached
    assert got == "All done, sir."
