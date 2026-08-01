"""Perplexity deep-research via the user's *account* (no API key).

Perplexity has no free API, so we drive the real web app in a headless Chrome that keeps a
persistent profile — the user logs in ONCE (`python -m jarvis --perplexity-login`) and the
session (cookies) is remembered at ~/.config/jarvis/perplexity-profile. After that, `research()`
runs queries as that logged-in account (Pro / deep-research features included). It also works
anonymously (logged-out) for basic answers, just without Pro.

Selectors on perplexity.ai drift over time; extraction is deliberately fuzzy and falls back to
grabbing the main answer prose, so it keeps working through minor UI changes.
"""

from __future__ import annotations

import os
from pathlib import Path

PROFILE = Path("~/.config/jarvis/perplexity-profile").expanduser()
URL = "https://www.perplexity.ai/"


def _clear_lock() -> None:
    import subprocess

    try:
        subprocess.run(["pkill", "-f", f"chrome.*--user-data-dir={PROFILE}"], capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (PROFILE / name).unlink()
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            pass


def _ctx(p, headless: bool, hidden: bool = False):
    PROFILE.mkdir(parents=True, exist_ok=True)
    _clear_lock()
    env_over, extra_args = ({}, [])
    if hidden:
        from .browser_env import launch_extras

        env_over, extra_args = launch_extras()
    return p.chromium.launch_persistent_context(
        str(PROFILE),
        channel="chrome",
        headless=headless,
        viewport={"width": 1280, "height": 900},
        env={**os.environ, **env_over} if env_over else None,
        args=["--disable-blink-features=AutomationControlled", *extra_args],
    )


def login() -> None:
    """Open a real window so the user can sign in once; the session persists in the profile."""
    from playwright.sync_api import sync_playwright

    print("Opening Perplexity — sign in (Google/email). Close the window when you're done.")
    with sync_playwright() as p:
        ctx = _ctx(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, timeout=60000)
        try:
            # block until the user closes the window
            page.wait_for_event("close", timeout=0)
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    print("Saved. Jarvis can now research as your Perplexity account.")


def is_logged_in() -> bool:
    """Best-effort: a logged-in profile has an auth cookie."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            ctx = _ctx(p, headless=False, hidden=True)
            cookies = ctx.cookies()
            ctx.close()
        return any("session" in (c.get("name", "").lower()) or "__Secure" in c.get("name", "") for c in cookies)
    except Exception:  # noqa: BLE001
        return False


def _research_sync(query: str, headless: bool = False, timeout_s: int = 75) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _ctx(p, headless=headless, hidden=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(URL, timeout=45000)
            # find the ask box (textarea or contenteditable) and submit
            box = None
            for sel in ["textarea", "[contenteditable='true']", "[role='textbox']"]:
                try:
                    box = page.wait_for_selector(sel, timeout=8000)
                    if box:
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not box:
                return {"ok": False, "text": "Couldn't find Perplexity's search box (UI may have changed)."}
            box.click()
            # works for both <textarea> and contenteditable boxes
            page.keyboard.insert_text(query)
            page.wait_for_timeout(100)
            page.keyboard.press("Enter")

            # wait for the answer to appear and stop growing (streaming finished)
            page.wait_for_timeout(800)
            answer, stable = "", 0
            import time as _t

            start = _t.time()
            while _t.time() - start < timeout_s:
                txt = _extract(page)
                if txt and txt == answer and len(txt) > 0:
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                    answer = txt
                page.wait_for_timeout(400)

            sources = _sources(page)
            if not answer:
                return {"ok": False, "text": "Perplexity returned no readable answer (login may be required)."}
            return {"ok": True, "text": answer, "sources": sources}
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


def _extract(page) -> str:
    js = """
    () => {
      const proses = Array.from(document.querySelectorAll('.prose, [class*=prose], [class*=answer]'));
      let best = '';
      for (const el of proses) { const t = el.innerText || ''; if (t.length > best.length) best = t; }
      return best.trim();
    }"""
    try:
        return page.evaluate(js) or ""
    except Exception:  # noqa: BLE001
        return ""


def _sources(page) -> list[str]:
    js = """
    () => Array.from(document.querySelectorAll('a[href^="http"]'))
      .map(a => a.href)
      .filter(h => !h.includes('perplexity.ai'))
      .slice(0, 8)"""
    try:
        seen, out = set(), []
        for h in page.evaluate(js) or []:
            if h not in seen:
                seen.add(h); out.append(h)
        return out[:6]
    except Exception:  # noqa: BLE001
        return []


# Cloudflare blocks headless; a visible window with the persistent profile passes reliably.
# Override with JARVIS_PERPLEXITY_HEADLESS=1 if you've established clearance and want it hidden.
_HEADLESS = os.environ.get("JARVIS_PERPLEXITY_HEADLESS", "0").strip().lower() in ("1", "true", "yes")


_CONCISE = (" — answer concisely as a short brief: 2-4 sentences of the key takeaway, then up to 4 "
            "bullet points of the most important specifics. No preamble.")


async def research(query: str, headless: bool | None = None, concise: bool = True) -> dict:
    """Async wrapper — runs the sync Playwright flow in a worker thread.

    The summary is produced by Perplexity itself (we ask it to be concise), so there's no dependency
    on any local model — the ChatGPT brain gets a tight, ready-to-relay brief.
    """
    import asyncio

    hl = _HEADLESS if headless is None else headless
    q = query + _CONCISE if concise else query
    return await asyncio.to_thread(_research_sync, q, hl)
