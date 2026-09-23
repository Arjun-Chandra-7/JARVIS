"""Streaming a reply, without changing the loop that reads it.

The agent loop reads `resp.choices[0].message`, branches on `.tool_calls`, and puts the message
back into the conversation. A streamed answer arrives as hundreds of fragments instead, so it is
reassembled into that exact shape — and these tests are mostly about that shape being right,
because the loop has no way to tell it is holding a reconstruction.

The property that matters most is the second group below: a turn that calls a tool must speak
nothing. Speaking a model's preamble before it calls a tool would say out loud something the
user was never meant to hear, and unlike a wrong shape it cannot be taken back.

Verified separately against a live model (local Ollama, no API cost): a plain answer reassembled
exactly and could have started being spoken 1.23 s before it was finished, and a tool-calling
turn produced the call with zero fragments forwarded.
"""

from __future__ import annotations

import pytest

from jarvis.agent.groq_core import GroqAgent


# --------------------------------------------------------------------------- fake stream parts
class Fn:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class Call:
    def __init__(self, index, call_id=None, name=None, arguments=None):
        self.index = index
        self.id = call_id
        self.function = Fn(name, arguments)


class Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class Choice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class Chunk:
    def __init__(self, delta=None, finish_reason=None):
        self.choices = [Choice(delta, finish_reason)] if delta is not None else []


class Brain:
    """Just enough of the agent to drive `_stream`."""

    _stream = GroqAgent._stream

    def __init__(self, chunks):
        self._chunks = chunks
        self.model = "test-model"
        self.messages = []
        self._specialist = None

        class C:
            temperature = 0.4
        self.config = C()

        outer = self

        class Completions:
            @staticmethod
            def create(**_kwargs):
                return iter(outer._chunks)

        class Chat:
            completions = Completions()

        class Client:
            chat = Chat()

        self.client = Client()

    def _active_schemas(self):
        return []


def text(*pieces, finish="stop"):
    return [Chunk(Delta(content=p)) for p in pieces] + [Chunk(Delta(content=None), finish)]


# --------------------------------------------------------------------------- a plain answer
def test_fragments_are_forwarded_as_they_arrive():
    spoken = []
    resp = Brain(text("Good ", "morning", ", sir."))._stream(spoken.append)
    assert spoken == ["Good ", "morning", ", sir."]
    assert resp.choices[0].message.content == "Good morning, sir."


def test_the_reassembled_message_has_no_tool_calls():
    """None, not an empty list: the loop branches on truthiness and the API sends None."""
    resp = Brain(text("All done."))._stream(lambda _p: None)
    assert resp.choices[0].message.tool_calls is None
    assert resp.choices[0].message.role == "assistant"


def test_the_finish_reason_survives():
    resp = Brain(text("Done."))._stream(lambda _p: None)
    assert resp.choices[0].finish_reason == "stop"


def test_chunks_with_no_choices_are_ignored():
    """Keep-alives and usage-only chunks arrive with an empty choices list."""
    chunks = [Chunk(), Chunk(Delta(content="Hello.")), Chunk()]
    resp = Brain(chunks)._stream(lambda _p: None)
    assert resp.choices[0].message.content == "Hello."


# ------------------------------------------------------------------- a turn that calls a tool
def test_a_tool_call_is_reassembled_from_its_fragments():
    """Arguments arrive a few characters at a time and are concatenated, not parsed — the loop
    expects the raw JSON string, exactly as the API sends it."""
    chunks = [
        Chunk(Delta(tool_calls=[Call(0, "call_1", "open_app", '{"na')])),
        Chunk(Delta(tool_calls=[Call(0, None, None, 'me": "Firefox"}')])),
        Chunk(Delta(content=None), "tool_calls"),
    ]
    resp = Brain(chunks)._stream(lambda _p: None)
    calls = resp.choices[0].message.tool_calls
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].function.name == "open_app"
    assert calls[0].function.arguments == '{"name": "Firefox"}'


def test_nothing_is_spoken_once_a_tool_call_appears():
    """The property that cannot be taken back. A model that says "Let me open that for you"
    and then calls a tool must not have said it out loud."""
    spoken = []
    chunks = [
        Chunk(Delta(tool_calls=[Call(0, "call_1", "open_app", "{}")])),
        Chunk(Delta(content="Let me open that for you.")),
        Chunk(Delta(content=None), "tool_calls"),
    ]
    Brain(chunks)._stream(spoken.append)
    assert spoken == []


def test_speaking_stops_at_the_call_even_if_text_came_first():
    """A model that starts talking and then calls a tool: what was already said cannot be
    unsaid, but nothing after the call is added to it."""
    spoken = []
    chunks = [
        Chunk(Delta(content="One moment.")),
        Chunk(Delta(tool_calls=[Call(0, "call_1", "open_app", "{}")])),
        Chunk(Delta(content=" Opening it now.")),
        Chunk(Delta(content=None), "tool_calls"),
    ]
    Brain(chunks)._stream(spoken.append)
    assert spoken == ["One moment."]


def test_several_tool_calls_keep_their_order():
    chunks = [
        Chunk(Delta(tool_calls=[Call(1, "call_b", "second", "{}")])),
        Chunk(Delta(tool_calls=[Call(0, "call_a", "first", "{}")])),
        Chunk(Delta(content=None), "tool_calls"),
    ]
    resp = Brain(chunks)._stream(lambda _p: None)
    assert [c.function.name for c in resp.choices[0].message.tool_calls] == ["first", "second"]


def test_content_alongside_a_call_is_kept_but_not_spoken():
    """Kept, because the loop may still want it; not spoken, because the turn is not an answer."""
    spoken = []
    chunks = [
        Chunk(Delta(tool_calls=[Call(0, "call_1", "open_app", "{}")])),
        Chunk(Delta(content="thinking")),
        Chunk(Delta(content=None), "tool_calls"),
    ]
    resp = Brain(chunks)._stream(spoken.append)
    assert spoken == []
    assert resp.choices[0].message.content == "thinking"


# --------------------------------------------------------------------------- degenerate cases
def test_an_empty_answer_becomes_none_not_an_empty_string():
    """An empty string beside a tool call means something different to the API than no content."""
    resp = Brain([Chunk(Delta(content=None), "stop")])._stream(lambda _p: None)
    assert resp.choices[0].message.content is None


def test_no_callback_is_allowed():
    """Callers that only want the answer should not have to invent a callback."""
    resp = Brain(text("Fine."))._stream(None)
    assert resp.choices[0].message.content == "Fine."


def test_a_call_with_no_name_is_dropped():
    """A fragment that never carried a function name is not a call; passing it on would put a
    nameless tool into the conversation."""
    chunks = [
        Chunk(Delta(tool_calls=[Call(0, "call_1", None, "{}")])),
        Chunk(Delta(content=None), "tool_calls"),
    ]
    resp = Brain(chunks)._stream(lambda _p: None)
    assert resp.choices[0].message.tool_calls is None
