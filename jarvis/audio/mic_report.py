"""«is my microphone working» — answered, rather than discovered the hard way.

Everything needed to answer this already existed: the mute check, the probe, the recovery ladder.
What was missing was a way to *ask*. The ladder only runs after Jarvis has already failed to hear
something, so the way you found out your microphone was muted was by being told "sorry sir, I
didn't catch that" until you gave up — which is how it actually went.

This reports, and where it can, fixes. The distinction matters both ways round: saying the
microphone is fine when it is silent wastes somebody's afternoon, and saying it is broken when
the room is simply quiet sends them to fix a thing that was never wrong.
"""

from __future__ import annotations

import re
from typing import Optional

from . import inputs

# Long enough to hear a room, short enough to say out loud without a pause. The idle probe the
# ladder uses is 0.6 s, which is right for a between-turns check and too short to characterise a
# noise floor.
LISTEN_S = 1.5

# Above this an idle microphone is not idle — it is amplifying its own noise into everything
# Whisper will ever be given. Measured on this machine at +30 dB boost on +30 dB capture: 0.84,
# where a healthy floor was 0.005.
SATURATED = 0.30

# Below this there is no signal at all: not a quiet room, a dead device.
DEAD = 1e-5


# Words that, next to "microphone", mean somebody is asking after it rather than mentioning it.
_ABOUT_ITS_STATE = re.compile(
    r"\b(work|works|working|broken|dead|ok|okay|fine|check|test|testing|hear|hearing|"
    r"listening|muted|mute|status|problem|issue|wrong|picking\s+up)\b")

# "Is my mic on" is a real way to ask and "on" is far too common a word to trust on its own —
# it matched "the microphone on this laptop is a cheap one". So it only counts inside something
# already shaped like a question.
_A_QUESTION = re.compile(r"^\s*(is|are|can|does|do|did|has|have|why|what|whats|what's)\b|\?\s*$")
_ON_OR_OFF = re.compile(r"\b(on|off)\b")


def asked(text: str) -> bool:
    """Whether this is a question about the microphone."""
    said = (text or "").strip().lower()
    if not said:
        return False
    if not re.search(r"\b(mic|mike|microphone)\b", said):
        return False
    if _ABOUT_ITS_STATE.search(said):
        return True
    return bool(_A_QUESTION.search(said) and _ON_OR_OFF.search(said))


def _describe(peak: float, muted: Optional[bool]) -> str:
    if muted:
        return "your microphone is muted"
    if peak <= DEAD:
        return "your microphone is producing complete silence, which means nothing is reaching it"
    if peak >= SATURATED:
        return ("your microphone is saturated — the gain is so high that speech arrives as "
                "noise")
    return "your microphone sounds fine"


_ASKED_ABOUT_MUTE = re.compile(r"\bmuted?\b")


def check(device_index: int = -1, said: str = "") -> str:
    """Listen, say what is true, and fix it when it is fixable.

    Ordered so the answer is the first thing said. Somebody asking this wants to know whether to
    keep talking, not to read a reading.
    """
    muted = inputs.is_muted()
    if muted:
        # The one cause a person can see, and the one worth acting on without being asked: the
        # answer to "is my mic working" is more useful when it comes with the fix already done.
        if inputs.unmute():
            heard = inputs.sample(device_index, seconds=LISTEN_S)
            if heard.peak > DEAD:
                return ("Your microphone was muted, sir. I've unmuted it and it's picking up "
                        "sound now.")
            return ("Your microphone was muted, sir. I've unmuted it, but nothing is reaching "
                    "it yet.")
        return ("Your microphone is muted, sir, and I wasn't able to unmute it — the mute key "
                "or Sound settings will do it.")

    heard = inputs.sample(device_index, seconds=LISTEN_S)
    verdict = _describe(heard.peak, muted)

    if heard.peak >= SATURATED:
        # Worth naming: it is the failure that looks like working, because the level meter moves.
        return (f"No, sir — {verdict}. Turning the input gain down, or switching off microphone "
                f"boost, is what fixes it.")
    if heard.peak <= DEAD:
        return (f"No, sir — {verdict}. Something else may have taken the device, or it is "
                f"switched off at the hardware.")
    if muted is None:
        # wpctl could not say. Honest about which half of the answer is missing.
        return ("Sound is reaching me, sir, so the microphone is working. I couldn't check "
                "whether it's muted at the system level.")
    # Answer the question that was asked. "Is it muted?" answered with "it's working" is true
    # and beside the point, and the point is the only reason anyone asks.
    if _ASKED_ABOUT_MUTE.search((said or "").lower()):
        return "No, sir — your microphone isn't muted, and it's picking up sound."
    return "Yes, sir — your microphone is working."
