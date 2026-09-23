"""Put text at the cursor, and only say so when it is seen there.

In order:

    1. AT-SPI       insert at the caret through the field's own EditableText, read back
    2. clipboard    save the clipboard, paste, read the field back where it can be read,
                    put the clipboard back — always, even when the paste fails
    3. keystrokes   ydotool typing, ASCII only (it cannot type Devanagari) and never verified

A method that may have changed the field is never followed by another: a paste after an
insertion that half-worked is how text appears twice. "pasted" without verification is reported
as exactly that, and the text stays in history so it can be pasted again.

Never into a password, OTP, PIN or token field. In a terminal: no newline, no Enter.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import clipboard as _clip
from .focus import BRIDGE


@dataclass
class Inserted:
    status: str             # inserted, pasted, typed, refused, failed
    method: str = ""        # atspi, clipboard, keys
    verified: bool = False
    start: int = -1
    end: int = -1
    reason: str = ""
    sent_ms: float = -1.0     # from the call to the text being delivered (before any read-back)

    @property
    def landed(self) -> bool:
        return self.status in {"inserted", "pasted", "typed"}


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _pasted(bridge, text: str, before_len: int, replaced: int, wait_s: float = 0.4) -> tuple[bool, int]:
    """Did the paste land? Read the field back for up to ``wait_s``: the caret where it can be
    read, else the field's tail (a terminal keeps its caret to itself)."""
    deadline = time.monotonic() + wait_s
    want = _squash(text)[-40:]
    while True:
        after = bridge.request("focus")
        if after.get("ok") and after.get("has_text"):
            length = after.get("length") or 0
            grew = length - before_len
            if grew >= len(text) - replaced:
                if _squash(after.get("before") or "").endswith(want):
                    return True, (after.get("caret") or 0) - len(text)
                tail = bridge.request("read", start=max(0, length - len(text) - 400), end=length)
                if tail.get("ok") and want in _squash(tail.get("text") or ""):
                    return True, -1
                if tail.get("ok") and not tail.get("text") and grew == len(text) - replaced:
                    # A terminal or GTK 4 view that reports its length but reads back as empty:
                    # grown by exactly the pasted text is the evidence there is.
                    return True, -1
        if time.monotonic() >= deadline:
            return False, -1
        time.sleep(0.05)


_BROWSER_CHECK = ("if (!document.hasFocus()) return null;"
                  "const e = document.activeElement; if (!e || e.type === 'password') return null;"
                  "return String(e.value ?? e.innerText ?? '').slice(-600);")


def _browser_has(text: str, app: str) -> bool:
    """Firefox and Zen do not update the accessible text after a paste. Their DOM does: read the
    focused element of the focused tab (never a password input), without bringing anything
    forward."""
    if not re.search(r"(?i)zen|firefox", app or ""):
        return False
    try:
        from ..integrations import marionette
        if not marionette.reachable():
            return False
        want = _squash(text)[-40:]
        with marionette.Connection(timeout=4) as conn:
            current = conn._send("WebDriver:GetWindowHandle")
            try:
                for handle in conn._send("WebDriver:GetWindowHandles") or []:
                    conn._send("WebDriver:SwitchToWindow", {"handle": handle, "focus": False})
                    value = conn.script(_BROWSER_CHECK)
                    if value is not None:
                        return want in _squash(value)
            finally:
                if current:
                    conn._send("WebDriver:SwitchToWindow", {"handle": current, "focus": False})
    except Exception:  # noqa: BLE001 — no answer is "could not confirm", never an error
        return False
    return False


def _later(delay: float, fn, *args) -> None:
    t = threading.Timer(delay, fn, args=args)
    t.daemon = True
    t.start()


def _secret(info: dict) -> bool:
    from .profiles import SECRET, SECRET_ROLES
    return bool(info.get("secret")) or info.get("role") in SECRET_ROLES or bool(SECRET.search(info.get("name") or ""))


def insert(text: str, info: Optional[dict] = None, *, terminal: bool = False, bridge=BRIDGE,
           clip=_clip, keys=None, saved=None, browser_check=None) -> Inserted:
    """``saved``: the clipboard as it was, already read (or a future of it) — reading it takes
    100–170 ms here, so the engine starts that while speech is still being recognised."""
    if not text:
        return Inserted("failed", reason="nothing to insert")
    browser_check = browser_check or _browser_has
    info = info if info is not None else bridge.request("focus")
    if info.get("ok") and _secret(info):
        return Inserted("refused", reason="That's a password or secret field — I won't type into it.")
    if terminal:
        text = text.replace("\r", " ").replace("\n", " ").rstrip()   # nothing that could run a line

    # 1. Accessibility insertion at the caret.
    t0 = time.monotonic()
    if info.get("ok") and info.get("can_insert") and not terminal:
        got = bridge.request("insert", text=text, replace_selection=True)
        if got.get("verified"):
            return Inserted("inserted", "atspi", True, got.get("start", -1), got.get("end", -1),
                            sent_ms=round((time.monotonic() - t0) * 1000, 1))
        grew = got.get("grew") or 0
        if grew <= 0 and got.get("ok"):
            # Firefox says yes to an insertion into a contenteditable or a search box and changes
            # nothing. Browsers can apply it a moment later, so look again before pasting — a
            # paste on top of a late insertion is how text appears twice.
            time.sleep(0.15)
            again = bridge.request("focus")
            grew = (again.get("length") or 0) - (info.get("length") or 0) if again.get("ok") else 0
        if grew > 0:
            # The field changed. Never paste on top of it; say whether it could be confirmed.
            return Inserted("inserted", "atspi", False, got.get("start", -1), got.get("end", -1),
                            sent_ms=round((time.monotonic() - t0) * 1000, 1),
                            reason="the field changed but could not be read back")
        # Nothing changed: the accessible insertion was refused in all but name. Paste instead.

    # 2. Clipboard paste, clipboard put back.
    if clip.available():
        before_len = info.get("length") if info.get("has_text") else None
        if saved is not None and hasattr(saved, "result"):
            try:
                saved = saved.result(timeout=1.0)
            except Exception:  # noqa: BLE001
                saved = None
        saved = saved if saved is not None else clip.save()
        restored = False
        try:
            if not clip.put(text):
                return Inserted("failed", reason="couldn't use the clipboard")
            if not clip.paste_keys(terminal=terminal):
                return Inserted("failed", reason="couldn't send the paste keys (is ydotoold running?)")
            sent = time.monotonic()
            verified, start = (_pasted(bridge, text, before_len, len(info.get("selected") or ""))
                               if before_len is not None else (False, -1))
            if not verified and not terminal:
                verified = browser_check(text, info.get("app", ""))
            # The app reads the clipboard when it handles the paste; give it that long before the
            # old contents go back — off this thread, so the caller is not kept waiting for it.
            _later(max(0.0, 0.25 - (time.monotonic() - sent)), clip.restore, saved)
            restored = True
            return Inserted("pasted", "clipboard", verified, start, start + len(text) if start >= 0 else -1,
                            sent_ms=round((sent - t0) * 1000, 1),
                            reason="" if verified else "pasted, but this app doesn't let me read it back")
        finally:
            if not restored:
                clip.restore(saved)

    # 3. Typing, last: ASCII only.
    if text.isascii():
        from ..integrations import desktop_control
        typer = keys or desktop_control
        if typer.type_text(text):
            return Inserted("typed", "keys", False, reason="typed, but I couldn't confirm it landed")
    return Inserted("failed", reason="no way to insert text here (no accessibility, clipboard or keyboard tool)")
