"""The tutor that comes up with study mode.

Entering study mode opens ChatGPT and hands it a standing brief: answer as an NCERT-grounded
CBSE examiner would, sized to the marks, in the textbook's own terminology. The point is that it
is there before the first question rather than being pasted in again every session.

The prompt lives in a file next to this one rather than in a string literal. It is a document —
twenty-seven numbered sections of exam policy — and the person who wants to change how their
tutor behaves should be able to open it and edit a heading, not hunt through Python for a triple
quoted block.

Sent once per session
---------------------
`start()` records that it has been sent, so a second sweep does not paste three thousand words
into the conversation again. Leaving study mode forgets it, because the next session wants the
brief at the top of a fresh conversation rather than buried under an hour of chemistry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

PROMPT_PATH = Path(__file__).with_name("exam_tutor_prompt.md")

CHATGPT = "https://chatgpt.com/"

_sent = False


def brief() -> str:
    """The standing instructions, from the file the user can edit."""
    try:
        return PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def already_sent() -> bool:
    return _sent


def forget() -> None:
    """Leaving study mode. The next session gets the brief at the top of a fresh conversation."""
    global _sent
    _sent = False


async def open_with_brief() -> tuple[bool, str]:
    """Open ChatGPT and type the brief into it. Returns (did it, what to say about it).

    Best effort throughout: study mode's job is closing distractions, and it must not fail to do
    that because a browser was slow. Every failure here returns False with a sentence explaining
    which step did not happen, rather than raising into the caller's sweep.
    """
    global _sent
    if _sent:
        return False, ""

    text = brief()
    if not text:
        return False, "I couldn't find the exam tutor brief to send."

    from ..integrations import browser

    ready = browser.ensure(CHATGPT)
    if not ready.get("ok"):
        # No control means the page can still be opened; it just cannot be typed into.
        try:
            from ..integrations import apps

            apps.open_url(CHATGPT)
            _sent = True
            return True, ("I've opened ChatGPT, but I can't type into it without browser "
                          "control, so the exam brief is yours to paste.")
        except Exception:  # noqa: BLE001
            return False, "I couldn't open ChatGPT."

    try:
        opened = await browser.open_site(CHATGPT)
        if not opened.get("ok", True):
            return False, opened.get("message") or "I couldn't open ChatGPT."
    except Exception:  # noqa: BLE001
        return False, "I couldn't open ChatGPT."

    typed = await _type_the_brief(text)
    _sent = True
    if typed:
        return True, "ChatGPT is up in exam tutor mode."
    return True, ("I've opened ChatGPT but couldn't type the brief in — it's in "
                  "jarvis/modes/exam_tutor_prompt.md if you want to paste it.")


async def _type_the_brief(text: str) -> bool:
    """Put the brief in the composer and send it."""
    from ..integrations import browser

    try:
        # The composer is a contenteditable, not an input, so it is found by its placeholder the
        # same way every other field on the page would be.
        for label in ("Ask anything", "Message ChatGPT", "Send a message"):
            result = await browser.type_into(label, text, submit=True)
            if result.get("ok"):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False
