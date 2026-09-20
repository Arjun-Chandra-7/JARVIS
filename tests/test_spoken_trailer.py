"""Taking the customer-service trailer off a spoken reply.

Every reply in the saved history ended the same way — "How can I assist you further?" — which
typed is padding and spoken is a whole extra sentence read aloud after every answer, during which
the voice loop cannot start listening again.
"""
from __future__ import annotations

import pytest

from jarvis.agent.spoken import trim_trailer


@pytest.mark.parametrize("reply,expected", [
    ("The colour of the sky varies by time and weather. How can I assist you further?",
     "The colour of the sky varies by time and weather."),
    ("IlluminaTech, NeoSphere, QuantumCore. How can I assist you further?",
     "IlluminaTech, NeoSphere, QuantumCore."),
    ("Done, sir. Anything else?", "Done, sir."),
    ("Opened Netflix. Let me know if you need anything else.", "Opened Netflix."),
    ("Sure. Anything else? Let me know if you need anything.", "Sure."),
    ("It's 22 degrees. Is there anything else I can help you with?", "It's 22 degrees."),
])
def test_the_offer_of_further_assistance_goes(reply, expected):
    assert trim_trailer(reply) == expected


@pytest.mark.parametrize("reply", [
    "Which Mum did you mean?",
    "I can help with that if you tell me the name.",
    "Do you want me to send it now?",
    "There are three Aroras in your contacts — which one?",
])
def test_a_real_question_survives(reply):
    """Jarvis asking something it actually needs answered is not padding."""
    assert trim_trailer(reply) == reply


def test_a_reply_that_is_only_the_trailer_is_left_alone():
    """Trimming it to nothing turns a poor answer into no answer."""
    assert trim_trailer("How can I assist you further?") == "How can I assist you further?"


def test_empty_stays_empty():
    assert trim_trailer("") == ""
    assert trim_trailer(None) == ""
