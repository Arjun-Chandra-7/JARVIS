"""What the agent said back, and the links it printed.

Two small things that make the difference between watching a terminal and being told what
happened. The agents announce their results in prose and then, very often, print a URL — a
preview deployment, a dev server, a pull request — and that URL is the thing you actually want
next.
"""

from __future__ import annotations

import re
from typing import Optional

# A real URL, or the shorthand a dev server prints. Trailing punctuation is excluded so a link at
# the end of a sentence does not carry the full stop into the browser.
_URL = re.compile(r"""(?ix)
    \b(?:
        https?://[^\s<>"'`\]]+
      | (?:localhost|127\.0\.0\.1)(?::\d{2,5})?(?:/[^\s<>"'`\]]*)?
    )""")

_TRAILING = ".,;:!?)]}'\"`"

# Links worth opening on their own. A dev server or a deployment is a result; a documentation
# link the agent quoted while explaining itself is not.
_WORTH_OPENING = re.compile(
    r"(?i)(localhost|127\.0\.0\.1|\bvercel\.app\b|\bnetlify\.app\b|\bngrok\b|"
    r"\bgithub\.com/[^/\s]+/[^/\s]+/pull/\d+|\bpages\.dev\b|\brender\.com\b|\bfly\.dev\b)")


def links(text: str) -> list[str]:
    """Every link in what the agent printed, in the order it printed them."""
    found = []
    for match in _URL.finditer(text or ""):
        url = match.group(0).rstrip(_TRAILING)
        if url and url not in found:
            found.append(url)
    return found


def worth_opening(text: str) -> Optional[str]:
    """The one link to open, or None.

    The last one wins when several qualify: agents print the deployment they just made after the
    ones they mentioned on the way there.
    """
    qualifying = [u for u in links(text) if _WORTH_OPENING.search(u)]
    if not qualifying:
        return None
    url = qualifying[-1]
    return url if "://" in url else f"http://{url}"


# Noise the agents put around their prose: box drawing, spinners, prompt markers, token counters.
_NOT_PROSE = re.compile(r"""(?x)
    ^\s*(?:
        [─-╿▀-▟\s]+       # box drawing and blocks
      | [>❯$#%]\s*                        # shell and agent prompts
      | (?:tokens?\s+used|esc\s+to|ctrl\+|press\s+\w+\s+to)\b.*
      | \[?\d+(?:,\d{3})*\s*tokens?\]?\s*
    )\s*$""", re.IGNORECASE)


# Progress chatter is short and punchy — "Building...", "✔ Deployed to <url>", "Local: <url>" —
# and an agent explaining what it did is not. Word count separates them better than any pattern:
# once the links are removed, a status line has almost nothing left and a sentence still does.
MIN_WORDS = 6


def _is_a_sentence(line: str) -> bool:
    without_links = _URL.sub("", line)
    words = [w for w in re.split(r"\s+", without_links) if any(c.isalpha() for c in w)]
    return len(words) >= MIN_WORDS


def spoken_answer(terminal_text: str, limit: int = 400) -> Optional[str]:
    """The agent's own words, short enough to say out loud.

    Read from the end backwards, because the answer is the last thing on screen, and stopping at
    the first run of prose long enough to be a sentence rather than a status line.
    """
    if not terminal_text:
        return None
    kept: list[str] = []
    for line in reversed(terminal_text.splitlines()):
        stripped = line.strip()
        if not stripped or _NOT_PROSE.match(stripped) or not _is_a_sentence(stripped):
            if kept:
                break                   # the prose has ended; what is above is older
            continue
        kept.append(stripped)
        if sum(len(k) for k in kept) > limit:
            break
    if not kept:
        return None
    said = " ".join(reversed(kept))
    # URLs are unspeakable and the link gets opened anyway.
    said = _URL.sub("the link", said)
    return " ".join(said.split())[:limit] or None
