"""Google Meet bot: join silently and take continuous live notes.

Usage (via Jarvis tool):
    await meet_bot.join_meet("meet.google.com/abc-defg-hij", "2 hours", config)
    # ... later ...
    notes = await meet_bot.stop_meet()

The bot joins with mic + camera off, reads Google Meet's live captions, accumulates
a transcript, and records participant arrivals and departures. It never sends chat
messages or other unsolicited meeting communication.

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

DEFAULT_MEET_URL = "https://meet.google.com/twa-pgjz-gss"


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
_status: dict = {"state": "idle", "detail": "", "url": "", "started_at": None}


def _ensure_profile() -> None:
    MEET_PROFILE.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)


def _notes_file() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    return NOTES_DIR / f"meet_notes_{ts}.txt"


def status() -> dict:
    """Return a serialisable snapshot for a HUD/API without exposing transcript content."""
    active = _bot_task is not None and not _bot_task.done()
    return {
        "active": active,
        "state": _status["state"],
        "detail": _status["detail"],
        "url": _status["url"],
        "started_at": _status["started_at"],
        "notes_path": str(_notes_path) if _notes_path else None,
    }


def _admission_state(page) -> tuple[str, str]:
    """Classify Meet UI state after requesting admission.

    A Join-button click merely requests admission for many meetings.  We only call
    the bot admitted once an in-call control is visible; waiting-room and rejection
    states are intentionally kept distinct.
    """
    def visible(selector: str) -> bool:
        try:
            return page.locator(selector).first.is_visible(timeout=250)
        except Exception:  # noqa: BLE001
            return False

    if any(visible(s) for s in (
        "button[aria-label*='Leave call' i]",
        "button[aria-label*='Leave meeting' i]",
        "[data-tooltip*='Leave call' i]",
    )):
        return "admitted", "In call"
    if any(visible(s) for s in (
        "text=You'll join the call when someone lets you in",
        "text=Asking to join",
        "text=Waiting for someone to let you in",
    )):
        return "waiting", "Waiting for a host to admit the bot"
    if any(visible(s) for s in (
        "text=You can't join this video call",
        "text=The meeting has ended",
        "text=You were removed from the call",
    )):
        return "rejected", "Not admitted to the meeting"
    return "unknown", "Admission has not been confirmed"


def _participant_snapshot(page) -> set[str]:
    """Best-effort visible participant names, robust to Meet's changing DOM classes."""
    names: set[str] = set()
    selectors = (
        "[data-participant-id] [aria-label]",
        "[data-requested-participant-id] [aria-label]",
        "[role='listitem'][aria-label]",
    )
    for selector in selectors:
        try:
            for element in page.query_selector_all(selector):
                label = (element.get_attribute("aria-label") or "").strip()
                if label and len(label) <= 120:
                    names.add(label)
        except Exception:  # noqa: BLE001
            continue
    return names


def _open_participant_panel(page) -> None:
    """Expose Meet's roster once so membership changes remain available to polling."""
    for selector in (
        "button[aria-label*='Show everyone' i]",
        "button[aria-label*='People' i]",
        "[data-tooltip*='Show everyone' i]",
    ):
        try:
            button = page.locator(selector).first
            if button.is_visible(timeout=500):
                button.click()
                return
        except Exception:  # noqa: BLE001
            continue


# --------------------------------------------------------------------------- sync playwright core

def _join_meet_sync(url: str, return_time: str, notes_path: str, stop_flag_file: str) -> str:
    """Runs in a thread: requests entry, verifies admission, then records notes."""
    from playwright.sync_api import sync_playwright
    import time

    _ensure_profile()

    # Normalise URL
    if not url.startswith("http"):
        url = "https://" + url

    transcript_lines: list[str] = []
    seen_captions: set[str]     = set()
    last_save                   = time.time()
    participants: set[str]      = set()
    last_participant_poll       = 0.0

    def _save_notes():
        try:
            Path(notes_path).parent.mkdir(parents=True, exist_ok=True)
            with open(notes_path, "w", encoding="utf-8") as f:
                f.write(f"Google Meet Notes\nJoined: {datetime.now():%Y-%m-%d %H:%M}\nURL: {url}\n")
                f.write("=" * 60 + "\n\n")
                f.write("\n".join(transcript_lines))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Meet bot: failed to save notes: %s", exc)

    with sync_playwright() as p:
        ctx = None
        try:
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
                _status.update(state="failed", detail="No Join button found")
                return f"Could not find a Join button on {url}. The meet may require a Google sign-in or may not be live yet."

            # Meet can leave us in a waiting room after the button click.  Do not
            # claim success or start a recording until an actual in-call control is visible.
            admission = ("unknown", "Admission has not been confirmed")
            for _ in range(30):
                admission = _admission_state(page)
                if admission[0] in {"admitted", "rejected"}:
                    break
                page.wait_for_timeout(1000)
            if admission[0] != "admitted":
                _status.update(state=admission[0], detail=admission[1])
                transcript_lines.append(f"[{datetime.now():%H:%M}] -- {admission[1]} --")
                return admission[1]

            _status.update(state="recording", detail="Admitted; recording captions and attendance")
            logger.info("Meet bot: admission confirmed; watching captions.")
            transcript_lines.append(f"[{datetime.now():%H:%M}] -- Jarvis joined the meeting (observing) --")

            # Enable captions (c shortcut)
            try:
                page.keyboard.press("c")
                page.wait_for_timeout(1000)
            except Exception:
                pass
            _open_participant_panel(page)

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

                # Record membership changes continuously. Meet only exposes the
                # roster while its people panel is available, so this is best-effort.
                if now - last_participant_poll >= 5:
                    current = _participant_snapshot(page)
                    for name in sorted(current - participants):
                        transcript_lines.append(f"[{datetime.now():%H:%M}] -- participant joined: {name} --")
                    for name in sorted(participants - current):
                        transcript_lines.append(f"[{datetime.now():%H:%M}] -- participant left: {name} --")
                    participants = current
                    last_participant_poll = now

                # Save every 2 minutes
                if now - last_save > 120:
                    _save_notes()
                    last_save = now

                page.wait_for_timeout(1200)

        except Exception as exc:  # noqa: BLE001
            logger.error(f"Meet bot error: {exc}")
            transcript_lines.append(f"[ERROR] {exc}")
            _status.update(state="error", detail=str(exc))
        finally:
            _save_notes()
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass

    if _status["state"] == "recording":
        _status.update(state="stopped", detail="Recording stopped")
    return "\n".join(transcript_lines[-30:])


# --------------------------------------------------------------------------- async public API

async def join_meet(url: str, return_time: str, config=None) -> str:
    """Join a Google Meet and start taking notes in the background.

    If url is empty, uses the configured default Meet URL.
    Returns the path to the notes file, or an error string.
    """
    global _bot_task, _notes_path, _return_time, _transcript, _stop_event, _status

    # A known default keeps voice requests predictable. The active-tab detector
    # remains useful for callers that explicitly supply its result.
    if not url or not url.strip():
        url = DEFAULT_MEET_URL

    _return_time = return_time or "soon"
    _notes_path  = _notes_file()
    _stop_event  = asyncio.Event()
    _transcript  = []
    _status = {"state": "starting", "detail": "Opening Meet", "url": url, "started_at": datetime.now().isoformat(timespec="seconds")}

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
        _status.update(state="stopping", detail="Waiting for recorder to save notes")
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
