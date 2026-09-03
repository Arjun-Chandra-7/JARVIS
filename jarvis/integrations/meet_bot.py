"""Google Meet bot: join silently, take live notes, answer if asked where Arjun is.

Usage (via Jarvis tool):
    await meet_bot.join_meet("meet.google.com/abc-defg-hij", "2 hours", config)
    # ... later ...
    notes = await meet_bot.stop_meet()

The bot joins with mic + camera off, reads Google Meet's live captions, accumulates
a transcript, and if anyone mentions "Arjun" in the captions or chat, it types a
polite reply in the Meet chat panel.

Notes are saved to ~/.local/share/jarvis/meet_notes_<timestamp>.txt every 2 minutes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.meet_bot")


def _detect_active_meet() -> Optional[str]:
    """Try every available method to find an active Google Meet URL.

    Priority order:
    1. Browser window titles (xdotool / wmctrl) — fastest, works when Meet tab is open
    2. Clipboard — if the user just copied the link
    3. xdotool + xprop to read the URL bar of the active window
    """
    import re
    import subprocess
    import shutil

    MEET_RE = re.compile(r"https?://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}", re.I)
    CODE_RE = re.compile(r"\b([a-z]{3}-[a-z]{4}-[a-z]{3})\b", re.I)  # bare code like twa-pgjz-gss

    def _try(argv):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=4)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    # 1a. All window titles via wmctrl
    if shutil.which("wmctrl"):
        titles = _try(["wmctrl", "-l"])
        m = MEET_RE.search(titles)
        if m:
            return m.group(0)

    # 1b. All window titles via xdotool
    if shutil.which("xdotool"):
        wids = _try(["xdotool", "search", "--name", "meet.google.com"]).splitlines()
        for wid in wids[:5]:
            name = _try(["xdotool", "getwindowname", wid.strip()])
            m = MEET_RE.search(name)
            if m:
                return m.group(0)
            # Sometimes title is just the meet code
            m2 = CODE_RE.search(name)
            if m2:
                return f"https://meet.google.com/{m2.group(1)}"

        # Active window title
        active = _try(["xdotool", "getactivewindow", "getwindowname"])
        m = MEET_RE.search(active)
        if m:
            return m.group(0)
        m2 = CODE_RE.search(active)
        if m2 and "meet" in active.lower():
            return f"https://meet.google.com/{m2.group(1)}"

    # 2. Clipboard
    for argv in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b"]):
        if not shutil.which(argv[0]):
            continue
        clip = _try(argv)
        m = MEET_RE.search(clip)
        if m:
            return m.group(0)
        m2 = CODE_RE.search(clip)
        if m2:
            return f"https://meet.google.com/{m2.group(1)}"

    # 3. xdotool + xprop URL bar (Opera/Chrome store URL in window name on some desktops)
    if shutil.which("xprop") and shutil.which("xdotool"):
        wid = _try(["xdotool", "getactivewindow"])
        if wid:
            props = _try(["xprop", "-id", wid.strip(), "WM_NAME"])
            m = MEET_RE.search(props)
            if m:
                return m.group(0)

    return None

MEET_PROFILE = Path("~/.config/jarvis/meet-profile").expanduser()
NOTES_DIR    = Path("~/.local/share/jarvis").expanduser()

# Global bot state
_bot_task: Optional[asyncio.Task] = None
_notes_path: Optional[Path]       = None
_return_time: str                  = ""
_transcript: list[str]             = []
_stop_event: asyncio.Event         = asyncio.Event()


def _ensure_profile() -> None:
    MEET_PROFILE.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)


def _notes_file() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    return NOTES_DIR / f"meet_notes_{ts}.txt"


# --------------------------------------------------------------------------- sync playwright core

def _join_meet_sync(url: str, return_time: str, notes_path: str, stop_flag_file: str) -> str:
    """Runs in a thread. Joins the meet, watches captions, writes notes, replies if Arjun is mentioned."""
    from playwright.sync_api import sync_playwright
    import time, re

    _ensure_profile()

    # Normalise URL
    if not url.startswith("http"):
        url = "https://" + url

    transcript_lines: list[str] = []
    seen_captions: set[str]     = set()
    last_save                   = time.time()
    replied_to: set[str]        = set()  # dedup "Arjun" replies
    replied_count               = 0

    def _save_notes():
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write(f"Google Meet Notes\nJoined: {datetime.now():%Y-%m-%d %H:%M}\nURL: {url}\n")
            f.write("=" * 60 + "\n\n")
            f.write("\n".join(transcript_lines))

    with sync_playwright() as p:
        MEET_PROFILE.mkdir(parents=True, exist_ok=True)
        ctx = p.chromium.launch_persistent_context(
            str(MEET_PROFILE),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
            ]
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        try:
            logger.info(f"Meet bot: navigating to {url}")
            page.goto(url, timeout=60000)
            page.wait_for_timeout(4000)

            # Name entry (if asked)
            try:
                name_input = page.wait_for_selector('input[aria-label*="name" i], input[placeholder*="name" i]', timeout=4000)
                if name_input:
                    name_input.fill("Jarvis Assistant")
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # Turn off mic
            try:
                page.keyboard.press("Control+d") # shortcut to mute mic
                page.wait_for_timeout(500)
            except Exception:
                pass
            
            # Turn off camera
            try:
                page.keyboard.press("Control+e") # shortcut to mute cam
                page.wait_for_timeout(500)
            except Exception:
                pass

            # Click Join / Ask to join
            joined = False
            for selector in [
                "button:has-text('Join')", 
                "button:has-text('Ask to join')", 
                "span:has-text('Ask to join')",
                "span:has-text('Join now')"
            ]:
                try:
                    btn = page.wait_for_selector(selector, timeout=3000)
                    if btn:
                        btn.click()
                        joined = True
                        logger.info(f"Meet bot: clicked join via {selector}")
                        page.wait_for_timeout(5000)
                        break
                except Exception:
                    continue

            if not joined:
                return f"Could not find a Join button on {url}. The meet may require a Google sign-in or may not be live yet."

            logger.info("Meet bot: joined the call, watching captions.")
            transcript_lines.append(f"[{datetime.now():%H:%M}] -- Jarvis joined the meeting (observing) --")

            # Enable captions (c shortcut)
            try:
                page.keyboard.press("c")
                page.wait_for_timeout(1000)
            except Exception:
                pass

            # --- Main caption watching loop ---
            # --- Main caption watching loop ---
            while not Path(stop_flag_file).exists():
                now = time.time()

                # Read captions — Google Meet uses several class patterns over time
                caption_text = ""
                for sel in [
                    "[data-message-text]",
                    ".a4cQT",          # caption container (as of 2024)
                    ".CNusmb span",    # spoken text spans
                    "[jsname='tgaKEf']",
                    ".iOzk7",
                ]:
                    try:
                        els = page.query_selector_all(sel)
                        if els:
                            caption_text = " ".join((e.inner_text() or "") for e in els[-6:]).strip()
                            break
                    except Exception:
                        pass

                if caption_text and caption_text not in seen_captions:
                    seen_captions.add(caption_text)
                    line = f"[{datetime.now():%H:%M}] {caption_text}"
                    transcript_lines.append(line)

                    # Check if Arjun is mentioned
                    if re.search(r"\barjun\b", caption_text, re.I):
                        reply_key = caption_text[:60]
                        if reply_key not in replied_to and replied_count < 10:
                            replied_to.add(reply_key)
                            replied_count += 1
                            _send_chat_message(page, f"Arjun is not here right now — he'll be back in {return_time}.")

                # Save every 2 minutes
                if now - last_save > 120:
                    _save_notes()
                    last_save = now

                page.wait_for_timeout(1200)

        except Exception as exc:  # noqa: BLE001
            logger.error(f"Meet bot error: {exc}")
            transcript_lines.append(f"[ERROR] {exc}")
        finally:
            _save_notes()
            try:
                ctx.close()
            except Exception:
                pass

    return "\n".join(transcript_lines[-30:])


def _send_chat_message(page, text: str) -> None:
    """Type a message in the Google Meet chat panel."""
    try:
        # Open chat sidebar
        for label in ["Chat with everyone", "Chat", "Open chat"]:
            try:
                btn = page.get_by_label(label, exact=False).first
                if btn and btn.is_visible(timeout=2000):
                    btn.click()
                    page.wait_for_timeout(800)
                    break
            except Exception:
                pass
        # Find the chat input and type
        for sel in ["[aria-label='Send a message']", "textarea[placeholder]", "[contenteditable='true']"]:
            try:
                inp = page.wait_for_selector(sel, timeout=3000)
                if inp:
                    inp.click()
                    inp.fill(text)
                    page.keyboard.press("Enter")
                    logger.info(f"Meet bot: sent chat '{text[:60]}'")
                    return
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Meet bot: couldn't send chat message: {exc}")


# --------------------------------------------------------------------------- async public API

async def join_meet(url: str, return_time: str, config=None) -> str:
    """Join a Google Meet and start taking notes in the background.

    If url is empty, auto-detects the active Meet from the browser.
    Returns the path to the notes file, or an error string.
    """
    global _bot_task, _notes_path, _return_time, _transcript, _stop_event

    # --- Auto-detect URL if not provided ---
    if not url or not url.strip():
        detected = await asyncio.to_thread(_detect_active_meet)
        if not detected:
            return ("I couldn't find an active Google Meet in your browser. "
                    "Make sure the Meet tab is open, or copy the link and I'll pick it up from your clipboard.")
        url = detected

    _return_time = return_time or "soon"
    _notes_path  = _notes_file()
    _stop_event  = asyncio.Event()
    _transcript  = []

    stop_flag = str(_notes_path) + ".stop"
    try:
        Path(stop_flag).unlink(missing_ok=True)
    except Exception:
        pass

    async def _run():
        result = await asyncio.to_thread(
            _join_meet_sync, url, _return_time, str(_notes_path), stop_flag
        )
        logger.info(f"Meet bot finished. Notes at {_notes_path}")
        return result

    _bot_task = asyncio.create_task(_run())
    return str(_notes_path)


async def stop_meet() -> str:
    """Stop the meet bot and return the final notes."""
    global _bot_task, _notes_path

    # Signal the sync thread via sentinel file
    if _notes_path:
        stop_flag = str(_notes_path) + ".stop"
        try:
            Path(stop_flag).touch()
        except Exception:
            pass

    if _bot_task and not _bot_task.done():
        try:
            await asyncio.wait_for(_bot_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _bot_task.cancel()

    if _notes_path and Path(_notes_path).exists():
        try:
            return Path(_notes_path).read_text(encoding="utf-8")
        except Exception:
            pass
    return "Meeting ended. Notes were not saved (bot may not have started)."
