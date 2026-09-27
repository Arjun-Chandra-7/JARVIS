"""Current information from retrieved sources, never from model memory alone.

A freshness-sensitive request ("latest", "today", prices, scores, weather, "is X still …") is
answered in three steps:

1. **Search** — DuckDuckGo's HTML endpoint, the same one ``integrations.websearch`` already uses.
   No authenticated browser, no Perplexity session, nothing that meets a CAPTCHA: if the plain
   search fails, the answer says so rather than escalating to something that pretends to be a
   person. Results with their snippets, at most ``MAX_SOURCES``.
2. **Answer** — the model is given the snippets as numbered, untrusted reference material
   (short excerpts only — ``MAX_EXCERPT`` characters each, for copyright and for tokens), told to
   answer only from them, cite them as [1], [2], and mark anything it infers beyond them.
3. **Cite** — the reply ends with the sources and the time they were retrieved.

Results are cached per session: 15 minutes for "today/now/latest" questions, 6 hours otherwise.
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .cache import CACHE

MAX_SOURCES = 4
MAX_EXCERPT = 300
_ENDPOINT = "https://html.duckduckgo.com/html/"
_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Jarvis/1.0"
_BLOCK = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAGS = re.compile(r"<[^>]+>")
_VERY_FRESH = re.compile(r"(?i)\b(?:today|tonight|now|right now|live|latest|breaking|score|price|weather)\b")


@dataclass
class Source:
    title: str
    url: str
    snippet: str
    retrieved_at: float

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc.removeprefix("www.")


def _real_url(href: str) -> str:
    href = html.unescape(href)
    if "duckduckgo.com/l/" in href:
        q = parse_qs(urlparse(href).query).get("uddg")
        if q:
            return unquote(q[0])
    return href if href.startswith("http") else ""


def search(query: str, timeout: float = 12.0) -> list[Source]:
    """Search results with snippets. Empty on any failure — never raises."""
    import httpx
    try:
        resp = httpx.post(_ENDPOINT, data={"q": query}, headers={"User-Agent": _AGENT}, timeout=timeout,
                          follow_redirects=True)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return []
    if re.search(r"(?i)captcha|unusual traffic|anomaly", resp.text[:5000]) and "result__a" not in resp.text:
        return []                                  # a bot check: stop, do not try to get round it
    now = time.time()
    out, seen = [], set()
    for href, title, snippet in _BLOCK.findall(resp.text):
        url = _real_url(href)
        if not url or url in seen or "duckduckgo.com" in url:
            continue
        seen.add(url)
        clean = lambda s: " ".join(html.unescape(_TAGS.sub("", s)).split())  # noqa: E731
        out.append(Source(clean(title)[:120], url, clean(snippet)[:MAX_EXCERPT], now))
        if len(out) >= MAX_SOURCES:
            break
    return out


def gather(query: str, session: str = "local", searcher: Optional[Callable] = None) -> list[Source]:
    cached = CACHE.get("research", query.lower().strip(), session=session)
    if cached is not None:
        return cached
    sources = (searcher or search)(query)[:MAX_SOURCES]
    if sources:
        kind = "research_fresh" if _VERY_FRESH.search(query) else "research"
        from .cache import TTL
        CACHE.put("research", query.lower().strip(), sources, session=session, ttl=TTL[kind])
    return sources


def reference_block(sources: list[Source]) -> str:
    return "\n".join(f"[{i}] {s.title} ({s.domain}): {s.snippet[:MAX_EXCERPT]}" for i, s in enumerate(sources, 1))


SYSTEM = ("Answer the question using ONLY the numbered search excerpts provided as reference material. "
          "Cite each fact with its number, like [1]. If the excerpts don't answer it, say that plainly. "
          "If you add anything beyond them, start that sentence with 'Likely' so it reads as inference. "
          "Be brief. Match the user's language.")


def citation_footer(sources: list[Source]) -> str:
    at = time.strftime("%H:%M", time.localtime(sources[0].retrieved_at)) if sources else ""
    rows = "\n".join(f"[{i}] {s.title} — {s.url}" for i, s in enumerate(sources, 1))
    return f"\n\nSources (retrieved {at}):\n{rows}"


def unavailable(lang: str) -> str:
    if lang == "hinglish":
        return "Abhi main current sources retrieve nahi kar paaya, isliye yeh memory se guess nahi karunga. Thodi der baad try karo."
    return "I couldn't retrieve current sources just now, so I won't answer that from memory — it may be out of date."
