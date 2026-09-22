"""Is this tab something to learn from, or something to stop watching?

Study mode closes Instagram and Netflix outright — nobody opens either to revise. YouTube is the
hard one, because it is both the biggest distraction on the machine and where the lectures are.
Closing it wholesale makes study mode useless for studying; leaving it open makes it useless for
studying.

So YouTube is judged, and the judging has a shape:

    shorts      closed, always, no appeal
    known good  a lecture, a tutorial, a chapter walkthrough — left alone
    known bad   a vlog, a reaction, a highlights reel — closed
    unsure      asked

Shorts first and unconditionally
--------------------------------
A Short is not a lecture that happens to be brief. The format is the distraction: an endless feed
with no stopping point, which is the exact thing study mode exists to interrupt. So there is no
"educational Shorts" case, and it is checked before anything else so no title can argue its way
past it.

Why a deterministic layer before the model
------------------------------------------
Most titles are obvious, and a model call per tab means study mode's sweep costs a round trip
every few seconds for tabs it has already seen. The lists below settle the clear cases for
nothing; only genuine ambiguity reaches the brain, and the answer is remembered per video so the
same one is never asked about twice.

The bias, stated plainly: when nothing is confident, a tab is left open. Closing a lecture
someone is midway through is a much worse failure than leaving one distraction up, and an
assistant that shuts the wrong tab gets turned off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

# Closed on sight, whatever they are called.
ALWAYS_CLOSE_HOSTS = (
    "instagram.com", "netflix.com", "primevideo.com", "hotstar.com", "twitch.tv",
    "tiktok.com", "reddit.com", "9gag.com",
)

EDUCATIONAL_WORDS = (
    "lecture", "tutorial", "course", "lesson", "class ", "chapter", "ncert", "cbse", "jee",
    "neet", "board exam", "revision", "explained", "explanation", "derivation", "theorem",
    "proof", "solved", "solution", "numerical", "formula", "syllabus", "notes", "crash course",
    "one shot", "oneshot", "full course", "masterclass", "how to solve", "concept",
    "physics", "chemistry", "biology", "maths", "mathematics", "algebra", "geometry",
    "trigonometry", "calculus", "history", "geography", "civics", "economics", "science",
    "documentary", "lecture series", "walkthrough", "deep dive", "fundamentals", "basics of",
    "introduction to", "learn ", "study ", "exam", "question paper", "sample paper",
)

DISTRACTING_WORDS = (
    "vlog", "reaction", "react", "prank", "funny", "meme", "memes", "fails", "compilation",
    "highlights", "trailer", "teaser", "music video", "official video", "song", "lyrics",
    "gameplay", "playthrough", "montage", "unboxing", "haul", "asmr", "mukbang", "tier list",
    "drama", "beef", "exposed", "roast", "cringe", "try not to laugh", "shorts", "status",
    "edit", "whatsapp status", "full movie", "episode", "season", "web series",
)


@dataclass(frozen=True)
class Verdict:
    """What to do with a tab, and why — the why is what gets said out loud."""

    close: bool
    reason: str
    certain: bool = True


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().lstrip("www.")
    except ValueError:
        return ""


def is_youtube(url: str) -> bool:
    host = host_of(url)
    return host.endswith("youtube.com") or host == "youtu.be" or host.endswith(".youtube.com")


def is_short(url: str) -> bool:
    """A Short, however it was linked.

    The path form is the usual one; the `shorts` query parameter turns up on shared links. Both
    are the same feed.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if not is_youtube(url):
        return False
    path = (parsed.path or "").lower()
    if path.startswith("/shorts/") or path == "/shorts":
        return True
    return "shorts" in (parse_qs(parsed.query or "").get("feature") or [])


def video_id(url: str) -> str:
    """The video this tab is on, so a judgement can be remembered against it."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if host_of(url) == "youtu.be":
        return (parsed.path or "/").lstrip("/").split("/")[0]
    path = (parsed.path or "").lower()
    if path.startswith("/shorts/"):
        return parsed.path.split("/shorts/", 1)[1].split("/")[0]
    return (parse_qs(parsed.query or "").get("v") or [""])[0]


def _clean_title(title: str) -> str:
    """A tab title without the chrome the site adds to it."""
    text = (title or "").strip()
    text = re.sub(r"\s*-\s*YouTube\s*$", "", text, flags=re.I)
    text = re.sub(r"^\(\d+\)\s*", "", text)          # the unread-count prefix
    return text.strip()


def judge_title(title: str) -> Optional[bool]:
    """True educational, False distracting, None when the words do not settle it."""
    text = _clean_title(title).lower()
    if not text:
        return None
    good = sum(1 for word in EDUCATIONAL_WORDS if word in text)
    bad = sum(1 for word in DISTRACTING_WORDS if word in text)
    if good and not bad:
        return True
    if bad and not good:
        return False
    if good != bad:
        # Both kinds present — "physics meme", "exam reaction". Whichever says more, wins.
        return good > bad
    return None


def verdict_for(url: str, title: str) -> Verdict:
    """What study mode should do with this tab, before anything is asked of a model."""
    host = host_of(url)
    if any(bad in host for bad in ALWAYS_CLOSE_HOSTS):
        return Verdict(close=True, reason=f"{host} is not studying")

    if not is_youtube(url):
        return Verdict(close=False, reason="not a watching site")

    if is_short(url):
        # Checked before the title, so nothing can argue its way past it.
        return Verdict(close=True, reason="Shorts are blocked in study mode")

    settled = judge_title(title)
    if settled is True:
        return Verdict(close=False, reason="looks like a lecture")
    if settled is False:
        return Verdict(close=True, reason="not educational")
    return Verdict(close=False, reason="unsure from the title alone", certain=False)
