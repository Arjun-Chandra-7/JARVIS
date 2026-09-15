"""Web results as a list of places to try, rather than a paragraph about them.

The existing web_search tool asks DuckDuckGo's instant-answer API, which returns an abstract —
useful for "who is X", useless for "find me a site that does Y", because it never names a site
you can open. This returns titles and URLs.
"""

from __future__ import annotations

import html
import re
from typing import Optional

_ENDPOINT = "https://html.duckduckgo.com/html/"
_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Jarvis/1.0"
_RESULT = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAGS = re.compile(r"<[^>]+>")


def results(query: str, limit: int = 8, timeout: float = 20.0) -> list[dict]:
    """[{title, url}] for a query, best first. Empty when the search fails — never raises."""
    import httpx

    query = (query or "").strip()
    if not query:
        return []
    try:
        response = httpx.post(_ENDPOINT, data={"q": query},
                              headers={"User-Agent": _AGENT},
                              timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - no results is a clear outcome; a traceback is not
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for url, title in _RESULT.findall(response.text):
        url = html.unescape(url)
        if not url.startswith("http"):
            continue
        host = re.sub(r"^https?://(?:www\.)?([^/]+).*", r"\1", url).lower()
        if host in seen:                 # one entry per site: five pages of the same one is not
            continue                     # five options
        seen.add(host)
        out.append({"title": _TAGS.sub("", html.unescape(title)).strip(), "url": url,
                    "host": host})
        if len(out) >= limit:
            break
    return out
