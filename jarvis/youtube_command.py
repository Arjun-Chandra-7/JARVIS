"""«search for X» on YouTube, then «play the first video» — without driving the browser.

Found on the end-to-end voice run: "Jarvis, open YouTube" worked, then "search for Pythagoras
theorem" matched no handler and went to the model, which answered with something the non-sequitur
check threw away ("Sorry sir, I didn't catch that"); and "play the first video" was read as
«open "first video"» and became a web search for those two words. The browser was not under
automation, so nothing could be clicked — but neither step needs clicking:

* the results page is a URL, opened like any other;
* the results themselves come from yt-dlp's search, fetched in the background while the page
  opens, and recorded in the conversation's context so "the first one", "the second video" and
  "the last one" mean something.

The reply says what was opened — a title — and never that it is playing, which Jarvis cannot see
from here.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import urllib.parse
from typing import Optional

RESULTS = 5
_pending: dict[str, "asyncio.Task"] = {}
_found: dict[str, list[tuple[str, str]]] = {}      # session → [(url, title)]

_SEARCH = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|now|and|then)[,\s]+)?(?:please\s+)?"
    r"(?:search|look\s+up|find)(?:\s+(?:youtube|on\s+youtube|in\s+youtube))?(?:\s+for)?\s+"
    r"(?P<q>.+?)(?:\s+(?:on|in)\s+youtube)?(?:\s+please)?[.!?]*$")
_PLAY_NTH = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|now|and|then)[,\s]+)?(?:please\s+)?(?:play|open|watch|start|put\s+on)"
    r"\s+(?:the\s+)?(?P<which>first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th|top)"
    r"(?:\s+(?:one|video|result|link))?(?:\s+please)?[.!?]*$")
_PLAY_URL = re.compile(r"(?i)^(?:play|open|watch|start|put\s+on)\s+"
                       r"(?P<url>https://www\.youtube\.com/watch\?v=[\w-]{11})[.!?]*$")
_ORDINAL = {"first": 1, "1st": 1, "top": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
            "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "last": -1}


def results_url(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)


def _on_youtube(session: str) -> bool:
    from . import context
    ctx = context.of(session)
    if not ctx.fresh():
        return False
    return any("youtube" in (v or "").lower() for v in (ctx.site, ctx.target, ctx.app))


def top_results(query: str, n: int = RESULTS, timeout: float = 12.0) -> list[tuple[str, str]]:
    """[(watch URL, title)] for the first ``n`` results, from yt-dlp. [] when it cannot."""
    from .screen.youtube import _ytdlp

    exe = _ytdlp()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--flat-playlist", "--no-warnings", "--print", "%(id)s|%(title)s",
                              f"ytsearch{n}:{query}"], capture_output=True, text=True,
                             timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        vid, _, title = line.partition("|")
        if re.fullmatch(r"[\w-]{11}", vid.strip()):
            found.append((f"https://www.youtube.com/watch?v={vid.strip()}", title.strip()))
    return found


async def _fetch(session: str, query: str) -> list[tuple[str, str]]:
    found = await asyncio.to_thread(top_results, query)
    _found[session] = found
    from . import context
    context.note_results([url for url, _ in found], session)
    return found


async def handle(text: str, config=None) -> Optional[str]:
    from . import context
    from .integrations import apps

    session = context.current()
    said = (text or "").strip()

    # context.resolve has already turned "play the first video" into "play <its URL>" when the
    # results were in: the same video, said with its title.
    resolved = _PLAY_URL.match(said)
    if resolved:
        url = resolved.group("url")
        title = next((t for u, t in _found.get(session, []) if u == url), "")
        if title:
            if not await asyncio.to_thread(apps.open_url, url):
                return "I couldn't open that video, sir."
            context.note_opened(session, site="youtube", target=title)
            context.note_action(said, title, session)
            return f"Opening “{title}”."

    nth = _PLAY_NTH.match(said)
    if nth and (session in _pending or session in _found):
        task = _pending.get(session)
        found = _found.get(session, [])
        if task is not None and not task.done():
            try:
                found = await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
            except asyncio.TimeoutError:
                return "The search results are still loading, sir — ask me again in a moment."
        if not found:
            return "I couldn't read the search results, sir, so I can't tell which video is first."
        n = _ORDINAL[nth.group("which").lower()]
        if n != -1 and n > len(found):
            return f"There are only {len(found)} results I can see, sir."
        url, title = found[-1] if n == -1 else found[n - 1]
        if not await asyncio.to_thread(apps.open_url, url):
            return "I couldn't open that video, sir."
        context.note_opened(session, site="youtube", target=title or url)
        context.note_action(said, title or url, session)
        return f"Opening “{title}”." if title else "Opening that video."

    search = _SEARCH.match(said)
    if not search:
        return None
    explicit = "youtube" in said.lower()
    if not explicit and not _on_youtube(session):
        return None
    query = search.group("q").strip().strip("\"'“”")
    if not query or query.lower() in {"it", "that", "this", "youtube"}:
        return None
    if not await asyncio.to_thread(apps.open_url, results_url(query)):
        return f"I couldn't open the YouTube search for {query}, sir."
    context.note_opened(session, site="youtube", target="youtube")
    context.note_action(said, query, session)
    _found.pop(session, None)
    _pending[session] = asyncio.create_task(_fetch(session, query))
    return f"Searching YouTube for {query}."
