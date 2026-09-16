"""Replies that say a thing was done when it was not. All of these are from the log.

    you> See you, daddy.          jarvis> Message sent to Daddy.
    you> dictation, go Don.       jarvis> Image generated. Ready.
    you> generate an image of a   jarvis> Image generated. Ready.

No message was sent and no image was generated.
"""
from __future__ import annotations

import pytest

from jarvis.agent.action_claims import claims_an_action


@pytest.mark.parametrize("reply", [
    "Message sent to Daddy.",
    "Image generated. Ready.",
    "Email sent.",
    "Reminder set for 5pm.",
    "Picture saved to your folder.",
    "Timer set.",
    "Screenshot saved.",
    "Post scheduled.",
])
def test_a_claim_with_the_object_in_front_is_still_a_claim(reply):
    """"I sent the message" was caught and "Message sent" was not — the same lie with the words
    in the other order, and the shape a small model actually uses to report a job done."""
    assert claims_an_action(reply) is True


def test_good_manners_do_not_excuse_a_false_claim():
    """"Message sent to Daddy. How may I assist you further, Sir?" read as honest, because the
    courtesy contains "may I", which is on the not-a-claim list. The exemption has to sit in the
    same sentence as the claim to excuse it."""
    assert claims_an_action("Message sent to Daddy. How may I assist you further, Sir?") is True
    assert claims_an_action("Image generated. Ready. Anything else I can do?") is True


@pytest.mark.parametrize("reply", [
    "I can open that for you.",
    "Would you like me to send a message?",
    "I could not send the message.",
    "I am not able to generate images.",
    "Your calendar appears to be clear for today.",
    "The message you received is from Maya.",
    "There are no upcoming events.",
    "That image looks like a fox.",
    "I will open Netflix if you want.",
])
def test_these_are_not_claims(reply):
    assert claims_an_action(reply) is False


def test_an_offer_in_the_same_sentence_still_excuses_it():
    assert claims_an_action("I can send a message to Daddy if you like.") is False
