"""Who is asking, and which words are theirs.

Two questions every request that changes Jarvis itself has to answer first.

**Source.** A turn arrives with a session id. The owner's own front-ends — the voice loop,
the overlay, the local web UI and the terminal — are trusted to change settings and to ask for
a repair. Everything else is not: the WhatsApp and Telegram bridges, away-mode conversations,
callers, and any session id this module has never heard of. The web server only listens on
127.0.0.1 behind Host/Origin checks, so a local process claiming "voice" already has the
owner's authority; that is the boundary, and it is documented as such (docs/SELF_REPAIR.md).

**Words.** A trusted turn can still carry untrusted text: the voice loop attaches the last
incoming message and a description of the screen in [brackets]; the overlay fences an attached
selection between "[… the user is referring to]" and "[end of …]". That text is evidence at
most — never an instruction. :func:`own_words` removes all of it, including a bracketed block
that spans several lines because the message inside it had line breaks, which used to leak
the message's later lines into the command.
"""
from __future__ import annotations

import re

TRUSTED_SOURCES = frozenset({"voice", "local", "overlay", "cli", "web"})
UNTRUSTED_SOURCES = frozenset({"whatsapp", "telegram", "instagram", "sms", "email", "call", "phone_call",
                               "away", "screen", "ocr", "browser", "document", "tool", "model"})


def is_trusted(source: str) -> bool:
    """May this source change settings or ask for a repair? Unknown means no."""
    return (source or "").strip().lower() in TRUSTED_SOURCES


_FENCE = re.compile(r"(?ims)^\s*\[[^\]\n]*\breferring to\]\s*$.*?^\s*\[end of [^\]\n]*\]\s*$")


def own_words(text: str) -> str:
    """Only what the person said: attached context, fenced selections and model notes removed."""
    text = _FENCE.sub("", text or "")
    kept: list[str] = []
    closing = ""
    for line in text.splitlines():
        stripped = line.strip()
        if closing:
            # Inside a bracketed block that began on an earlier line: skip through its end.
            if stripped.endswith(closing):
                closing = ""
            continue
        if stripped.startswith(("[", "(")):
            close = "]" if stripped.startswith("[") else ")"
            if close not in stripped:
                # Opened and not closed on this line: attached text with line breaks in it. A
                # bracket that never closes drops the rest — attached text, all of it.
                closing = close
            continue
        kept.append(line)
    return " ".join(" ".join(kept).split())
