"""Open a separate Opera ChatGPT tab and submit the study instructions once."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..integrations import apps, browser

PROMPT_FILE = Path(__file__).with_name("study_prompt.txt")


async def open_and_prime() -> bool:
    opened = await asyncio.to_thread(apps.open_url, "https://chatgpt.com/", "opera")
    if not opened:
        return False
    target = None
    for _ in range(20):
        try:
            tabs = await browser._targets()
            matches = [tab for tab in tabs if "chatgpt.com" in tab.get("url", "")]
            if matches:
                target = matches[-1]
                break
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.5)
    if target is None:
        return False
    prompt = PROMPT_FILE.read_text()
    try:
        browser.focus_on(target["id"])
        ws = await browser._connect(target)
        try:
            page = browser._Session(ws)
            await page.call("Runtime.enable")
            await page.call("Page.bringToFront")
            for _ in range(20):
                ready = await page.js("(() => {const el=document.querySelector('#prompt-textarea, [contenteditable=\"true\"]'); if(!el) return false; el.focus(); return true})()")
                if ready:
                    break
                await asyncio.sleep(0.5)
            else:
                return False
            await page.call("Input.insertText", {"text": prompt})
            landed = await page.js("(() => document.activeElement?.textContent?.length || 0)()")
            if not isinstance(landed, (int, float)) or landed < len(prompt) // 2:
                return False
            for kind in ("keyDown", "keyUp"):
                await page.call("Input.dispatchKeyEvent", {"type": kind, "key": "Enter",
                                                         "code": "Enter", "windowsVirtualKeyCode": 13})
            return True
        finally:
            await ws.close()
    except Exception:  # noqa: BLE001 — report failure rather than claiming the prompt was sent
        return False
