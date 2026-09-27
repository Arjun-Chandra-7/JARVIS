"""Is it safe to look at, or draw over, what is on screen right now?

Shared by 3D Studio (before a capture) and the Study Companion's teaching overlay (before a
diagram is drawn). A password, one-time-code, banking, password-manager or private-chat screen,
or the lock screen, is refused. The verdict names a category ("a password or sign-in screen"),
never the words that triggered it, so it is safe to say aloud and to log.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable

_TITLE = [
    ("a password or sign-in screen", re.compile(r"(?i)\b(?:password|passcode|sign[\s-]?in|log[\s-]?in|"
                                                r"login|authenticat\w*|2fa|two[\s-]factor|verify\s+it'?s\s+you)\b")),
    ("a one-time code", re.compile(r"(?i)\b(?:otp|one[\s-]time\s+(?:password|code)|verification\s+code)\b")),
    ("a banking or payment screen", re.compile(r"(?i)\b(?:bank(?:ing)?|net\s?banking|upi|paytm|phonepe|"
                                               r"google\s?pay|gpay|paypal|credit\s+card|debit\s+card|wallet|"
                                               r"checkout|payment)\b")),
    ("a password manager", re.compile(r"(?i)\b(?:1password|bitwarden|keepass\w*|lastpass|seahorse|"
                                      r"passwords\s+and\s+keys|keyring)\b")),
    ("a private chat", re.compile(r"(?i)\b(?:whatsapp|telegram|signal|messenger|instagram\s+direct|"
                                  r"direct\s+messages?|google\s+messages|imessage)\b")),
    ("the lock screen", re.compile(r"(?i)\b(?:lock\s*screen|screen\s*lock(?:ed)?|unlock\s+to|gnome-shell\s+lock)\b")),
]
_TEXT = [
    ("a password field", re.compile(r"(?i)\b(?:password|passcode|pin\s*code|enter\s+pin)\b")),
    ("a one-time code", re.compile(r"(?i)\b(?:otp|one[\s-]time|verification\s+code|security\s+code)\b")),
    ("a banking or payment screen", re.compile(r"(?i)\b(?:cvv|cvc|ifsc|account\s+(?:no|number)|card\s+number|"
                                               r"available\s+balance|net\s?banking|upi\s+id)\b")),
    ("a private chat", re.compile(r"(?i)\b(?:type\s+a\s+message|last\s+seen|end-to-end\s+encrypted)\b")),
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_MASKED = re.compile(r"[•●*]{4,}")


@dataclass
class Verdict:
    ok: bool
    category: str = ""           # what kind of screen it looked like — safe to say and to log

    def reason(self, doing: str = "capture it") -> str:
        return f"That looks like {self.category}, so I won't {doing}." if not self.ok else ""


def check_title(app: str = "", title: str = "") -> Verdict:
    text = f"{app} {title}"
    for category, pattern in _TITLE:
        if pattern.search(text):
            return Verdict(False, category)
    return Verdict(True)


def check_words(words: Iterable[str]) -> Verdict:
    text = " ".join(str(w) for w in words)
    for category, pattern in _TEXT:
        if pattern.search(text):
            return Verdict(False, category)
    if _CARD.search(text):
        return Verdict(False, "a card or account number")
    if _MASKED.search(text):
        return Verdict(False, "a password field")
    return Verdict(True)


def screen_locked() -> bool:
    """GNOME's screen shield is up. Unknown (no GNOME, no D-Bus) counts as not locked."""
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return False
    try:
        out = subprocess.run([gdbus, "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path",
                              "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.GetActive"],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "true" in out.lower()


def current() -> Verdict:
    """The screen as it is now: the lock screen, then the focused window's title."""
    if screen_locked():
        return Verdict(False, "the lock screen")
    try:
        from .screen_context import active_window
        app, window = active_window()
    except Exception:  # noqa: BLE001 — no window information: nothing to refuse on
        return Verdict(True)
    return check_title(app, window)
