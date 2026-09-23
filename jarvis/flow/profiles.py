"""Which kind of field the words are going into, and so how they are shaped.

    messaging   WhatsApp, Telegram, Slack, Discord… natural, no formal full stop on one line
    email       Gmail, Outlook, Thunderbird… paragraphs, full punctuation
    document    editors, Docs, Notion, notes… paragraphs, lists, headings kept
    code        VS Code, JetBrains… literal: symbols, no capitals, no prose model
    terminal    any terminal… literal, previewed first, never followed by Enter
    search      a search box or address bar… a query, not a sentence
    prose       anything else

Decided from the application, the window title (which for a browser is the site) and the focused
field's role and label. A secret field — password, OTP, PIN, token, key — is never written to and
its contents are never read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TERMINAL_APPS = re.compile(r"(?i)gnome-terminal|kgx|ptyxis|konsole|kitty|alacritty|xterm|tilix|terminator|"
                            r"wezterm|foot|\bconsole\b|terminal")
_CODE_APPS = re.compile(r"(?i)\bcode\b|vscodium|codium|visual studio code|jetbrains|pycharm|intellij|"
                        r"webstorm|clion|sublime|zed|neovim|antigravity|cursor")
_MESSAGING = re.compile(r"(?i)whatsapp|telegram|slack|discord|messenger|instagram|signal|\bmessages\b|"
                        r"teams|element|beeper|chat\b")
_EMAIL = re.compile(r"(?i)gmail|outlook|thunderbird|evolution|proton\s*mail|\bmail\b|compose|inbox|new message")
_DOCUMENT = re.compile(r"(?i)libreoffice|writer|google docs|\bdocs\b|notion|obsidian|text editor|gedit|"
                       r"gnome-text-editor|onenote|word|notes|overleaf|document")
_SEARCH_NAME = re.compile(r"(?i)search|address\s+bar|enter\s+address|find|url")
SECRET = re.compile(r"(?i)password|passcode|pass\s*word|\botp\b|one[- ]time|verification\s+code|\bpin\b|"
                    r"\bcvv\b|\bcvc\b|secret|token|api\s*key|private\s+key|passphrase|2fa|two[- ]factor")
SECRET_ROLES = {"password text"}


@dataclass
class Target:
    app: str = ""
    window: str = ""
    role: str = ""
    name: str = ""                 # the field's accessible label, never its value
    editable: bool = True
    multi_line: bool = False
    secret: bool = False

    @property
    def is_secret(self) -> bool:
        return self.secret or self.role in SECRET_ROLES or bool(SECRET.search(self.name or ""))


def profile_for(t: Target) -> str:
    if t.role == "terminal" or _TERMINAL_APPS.search(t.app or ""):
        return "terminal"
    if _CODE_APPS.search(t.app or "") or _CODE_APPS.search(t.window or ""):
        return "code" if t.role != "terminal" else "terminal"
    if t.role in {"entry", "combo box"} and not t.multi_line and _SEARCH_NAME.search(t.name or ""):
        return "search"
    where = f"{t.window} {t.name}"
    if _MESSAGING.search(where):
        return "messaging"
    if _EMAIL.search(where):
        return "email"
    if _DOCUMENT.search(where) or _DOCUMENT.search(t.app or ""):
        return "document"
    return "prose"


# How the cleanup treats each profile.
CLEANUP_PROFILE = {"messaging": "messaging", "email": "email", "document": "document", "code": "code",
                   "terminal": "terminal", "search": "search", "prose": "prose"}
# Profiles where a model may polish text (never code or terminals).
MODEL_ALLOWED = {"messaging", "email", "document", "prose"}
