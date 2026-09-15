"""Find a picture of something to draw from.

Wikimedia Commons rather than an image search: it is keyless, it is stable, the licensing is
explicit, and for the things people actually ask to be drawn — a painting, an animal, a landmark —
it has a good photograph filed under the obvious name. An image search would return more, and
would also return whatever a scraped page happened to contain.

Nothing here is cached in the repository; downloads go to the user's cache directory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks for a real identifier rather than a library default, and refuses some that lie.
_AGENT = "Jarvis/1.0 (personal assistant; contact via repository owner)"
_CACHE = Path.home() / ".cache" / "jarvis" / "references"


def _slug(subject: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (subject or "").lower()).strip("-")[:60] or "reference"


def find(subject: str, timeout: float = 20.0) -> Optional[Path]:
    """A local path to a picture of `subject`, or None when nothing suitable was found."""
    import httpx

    subject = (subject or "").strip()
    if not subject:
        return None

    cached = _CACHE / f"{_slug(subject)}.img"
    if cached.exists() and cached.stat().st_size > 2048:
        return cached

    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": _AGENT},
                          follow_redirects=True) as client:
            hits = client.get(_API, params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"{subject} filetype:bitmap", "gsrnamespace": "6",
                "gsrlimit": "8", "prop": "imageinfo", "iiprop": "url|size",
                # Detail cannot exceed what the reference holds: at 800px the Mona Lisa yields
                # about five thousand contour points, at 1600 about seventeen thousand.
                "iiurlwidth": "1600",
            }).json()
            pages = (hits.get("query") or {}).get("pages") or {}
            best, best_area = None, 0
            for page in pages.values():
                info = (page.get("imageinfo") or [{}])[0]
                url = info.get("thumburl") or info.get("url")
                # Thumbnail URLs carry a query string after the extension, so anchoring the
                # check to the end of the string rejected every one of them.
                if not url or not re.search(r"\.(jpe?g|png)(?:$|[?#])", url, re.I):
                    continue
                # Prefer a large original: a thumbnail of a detailed painting loses the lines.
                area = int(info.get("width") or 0) * int(info.get("height") or 0)
                if area > best_area:
                    best, best_area = url, area
            if not best:
                return None

            data = client.get(best).content
            if len(data) < 2048:
                return None
            _CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
            return cached
    except Exception:  # noqa: BLE001 - no picture is a clear outcome; a traceback is not
        return None


def clear() -> None:
    for path in _CACHE.glob("*.img"):
        try:
            path.unlink()
        except OSError:
            pass
