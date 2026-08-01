"""Instagram DMs via the user's account (no API — Instagram's API is closed to this).

Same pattern as ChatGPT/Perplexity: drive instagram.com in a persistent, logged-in Chrome. The user
signs in ONCE (`python -m jarvis --instagram-login`) and the session persists. Reading DMs then runs
hidden (Xvfb/off-screen). Best-effort — Instagram's markup is obfuscated and bot-detection is
aggressive, so extraction is fuzzy and this can break when they change the site.
"""

from __future__ import annotations

import os
from pathlib import Path

PROFILE = Path("~/.config/jarvis/instagram-profile").expanduser()
INBOX = "https://www.instagram.com/direct/inbox/"


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


def _ctx(p, headless: bool, hidden: bool):
    PROFILE.mkdir(parents=True, exist_ok=True)
    _clear_lock()
    env_over, extra_args = ({}, [])
    if hidden:
        from .browser_env import launch_extras

        env_over, extra_args = launch_extras()
    return p.chromium.launch_persistent_context(
        str(PROFILE), channel="chrome", headless=headless,
        viewport={"width": 1280, "height": 900},
        env={**os.environ, **env_over} if env_over else None,
        args=["--disable-blink-features=AutomationControlled", *extra_args],
    )


def login() -> None:
    from playwright.sync_api import sync_playwright

    print("Opening Instagram — sign in, then close the window when your inbox loads.")
    with sync_playwright() as p:
        ctx = _ctx(p, headless=False, hidden=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/", timeout=60000)
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    print("Saved. Jarvis can now read your Instagram DMs.")


def _dms_sync(limit: int = 8) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _ctx(p, headless=False, hidden=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(INBOX, timeout=45000)
            page.wait_for_timeout(1500)
            body = (page.inner_text("body"))[:200].lower()
            if "log in" in body and "message" not in body:
                return {"ok": False, "text": "Instagram isn't logged in — run `--instagram-login`."}
            # each DM thread row is a listitem/link showing username + a message preview
            threads = page.evaluate(
                """() => {
                  const rows = Array.from(document.querySelectorAll('div[role="listitem"], a[href*="/direct/t/"]'));
                  const out = [];
                  for (const r of rows) {
                    const t = (r.innerText || '').trim();
                    if (t) out.push(t.replace(/\\n+/g, ' — '));
                  }
                  return out;
                }"""
            ) or []
            # de-dupe, keep the most recent few
            seen, clean = set(), []
            for t in threads:
                if t not in seen:
                    seen.add(t); clean.append(t)
            clean = clean[:limit]
            if not clean:
                return {"ok": True, "text": "No readable DM threads (inbox empty or layout changed)."}
            return {"ok": True, "text": "\n".join(f"- {t}" for t in clean)}
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


async def dms(limit: int = 8) -> dict:
    import asyncio

    return await asyncio.to_thread(_dms_sync, limit)
