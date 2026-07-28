"""ChatGPT-as-brain over the web app (no API key — uses the user's ChatGPT Pro account).

Drives chatgpt.com in a real, logged-in Chrome (persistent profile) via Playwright and keeps ONE
conversation open for the whole Jarvis session. `ask()` sends a message and returns ChatGPT's reply
text once streaming finishes. Cloudflare/login means it runs headful (a visible window) — the user
signs in once with `python -m jarvis --chatgpt-login`, and the session persists.

The async session is kept alive across turns by the ChatGPT brain (agent/chatgpt_core.py). A separate
sync `login()` is used only by the one-time CLI command.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

PROFILE = Path("~/.config/jarvis/chatgpt-profile").expanduser()
URL = "https://chatgpt.com/"

_INPUT_SELECTORS = ["#prompt-textarea", "div[contenteditable='true']", "textarea"]
_ASSISTANT = "[data-message-author-role='assistant']"


def _clear_profile_lock() -> None:
    """Remove stale Chrome singleton locks (and kill orphan chromes on this profile) so a crashed
    previous run can't wedge the profile with 'Opening in existing browser session'."""
    import subprocess

    try:
        subprocess.run(["pkill", "-f", f"chrome.*--user-data-dir={PROFILE}"],
                       capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (PROFILE / name).unlink()
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# one-time login (sync, for the CLI)                                           #
# --------------------------------------------------------------------------- #
def login() -> None:
    from playwright.sync_api import sync_playwright

    PROFILE.mkdir(parents=True, exist_ok=True)
    print("Opening ChatGPT — sign in to your account, then close the window when the chat is ready.")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, timeout=60000)
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    print("Saved. Jarvis will now think through your ChatGPT account.")


# --------------------------------------------------------------------------- #
# live async session (the brain)                                              #
# --------------------------------------------------------------------------- #
class ChatGPTSession:
    def __init__(self, headless: bool | None = None) -> None:
        env = os.environ.get("JARVIS_CHATGPT_HEADLESS", "0").strip().lower()
        self.headless = (env in ("1", "true", "yes")) if headless is None else headless
        self._pw = None
        self.ctx = None
        self.page = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        from .browser_env import launch_extras

        PROFILE.mkdir(parents=True, exist_ok=True)
        _clear_profile_lock()   # a leftover Chrome must not block us from opening the profile
        env_over, extra_args = launch_extras()
        self._pw = await async_playwright().start()
        self.ctx = await self._pw.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=self.headless,
            viewport={"width": 1280, "height": 900},
            env={**os.environ, **env_over} if env_over else None,
            args=["--disable-blink-features=AutomationControlled", *extra_args],
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        # start a FRESH temporary chat so no old conversation context leaks in
        await self.page.goto(URL + "?temporary-chat=true", timeout=60000)
        await self.page.wait_for_timeout(2500)

    async def close(self) -> None:
        try:
            if self.ctx:
                await self.ctx.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def logged_in(self) -> bool:
        try:
            for sel in _INPUT_SELECTORS:
                if await self.page.query_selector(sel):
                    return True
            body = (await self.page.inner_text("body"))[:400].lower()
            return "log in" not in body and "sign up" not in body
        except Exception:  # noqa: BLE001
            return False

    async def _input(self):
        for sel in _INPUT_SELECTORS:
            el = await self.page.query_selector(sel)
            if el:
                return el
        return None

    async def ask(self, text: str, timeout_s: int = 120) -> str:
        page = self.page
        box = await self._input()
        if not box:
            return "[chatgpt] couldn't find the message box — is the account logged in? Run --chatgpt-login."

        before = len(await page.query_selector_all(_ASSISTANT))
        await box.click()
        await page.keyboard.insert_text(text)          # fast, preserves newlines
        await page.wait_for_timeout(150)
        await page.keyboard.press("Enter")

        # wait for a new assistant message to appear
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < 30:
            if len(await page.query_selector_all(_ASSISTANT)) > before:
                break
            await page.wait_for_timeout(400)

        # wait for streaming to finish: assistant text stable + no stop button
        last, stable = "", 0
        while asyncio.get_event_loop().time() - start < timeout_s:
            await page.wait_for_timeout(700)
            nodes = await page.query_selector_all(_ASSISTANT)
            if not nodes:
                continue
            cur = (await nodes[-1].inner_text()).strip()
            streaming = await page.query_selector("[data-testid='stop-button']")
            if cur and cur == last and not streaming:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
                last = cur
        return last or "[chatgpt] no reply captured."

    async def new_chat(self) -> None:
        try:
            await self.page.goto(URL, timeout=45000)
            await self.page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass
