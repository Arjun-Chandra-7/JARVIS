"""What 3D Studio may look at, what it keeps, and what it writes down.

* ``check_capture`` refuses password, OTP, banking and private-chat screens — from the window
  title and from the words inside the *cropped* region (read locally). The verdict names a
  category ("a password field"), never the words that triggered it.
* ``quiet_notifications`` hides GNOME notification banners while a capture is taken, and puts the
  setting back exactly as it was.
* ``References`` owns reference files: cropped captures live in the project's ``refs/`` folder,
  full-screen frames are deleted the moment they are cropped, and "delete the references"
  overwrites and removes them.
* ``audit`` / ``security_event`` write one line of whitelisted, scrubbed fields. No OCR text,
  no pixels, no paths into a user's files.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

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
class PrivacyVerdict:
    ok: bool
    category: str = ""           # what kind of screen it looked like — safe to say and to log

    def reason(self) -> str:
        return f"That looks like {self.category}, so I won't capture it." if not self.ok else ""


def check_title(app: str = "", title: str = "") -> PrivacyVerdict:
    text = f"{app} {title}"
    for category, pattern in _TITLE:
        if pattern.search(text):
            return PrivacyVerdict(False, category)
    return PrivacyVerdict(True)


def check_words(words: Iterable[str]) -> PrivacyVerdict:
    text = " ".join(str(w) for w in words)
    for category, pattern in _TEXT:
        if pattern.search(text):
            return PrivacyVerdict(False, category)
    if _CARD.search(text):
        return PrivacyVerdict(False, "a card or account number")
    if _MASKED.search(text):
        return PrivacyVerdict(False, "a password field")
    return PrivacyVerdict(True)


def read_words(image_path: str) -> list[str]:
    """Local OCR of one image, words only. Returns [] if OCR is unavailable."""
    try:
        from ..vision import ocr
        return [w.text for w in ocr.read(image_path)]
    except Exception:  # noqa: BLE001 — no OCR means the title check is all we have
        return []


def check_capture(image_path: str, app: str = "", title: str = "", words: Optional[list[str]] = None) -> PrivacyVerdict:
    verdict = check_title(app, title)
    if not verdict.ok:
        return verdict
    return check_words(words if words is not None else read_words(image_path))


# ----------------------------------------------------------------------------- notifications
@contextlib.contextmanager
def quiet_notifications():
    """Hide notification banners for the moment of capture (GNOME), then restore the old value."""
    gs = shutil.which("gsettings")
    old = None
    if gs:
        try:
            old = subprocess.run([gs, "get", "org.gnome.desktop.notifications", "show-banners"],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
            if old == "true":
                subprocess.run([gs, "set", "org.gnome.desktop.notifications", "show-banners", "false"],
                               timeout=3, check=False)
                time.sleep(0.25)          # let a banner already on screen go
        except (OSError, subprocess.TimeoutExpired):
            old = None
    try:
        yield
    finally:
        if gs and old == "true":
            try:
                subprocess.run([gs, "set", "org.gnome.desktop.notifications", "show-banners", "true"],
                               timeout=3, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass


# ----------------------------------------------------------------------------- files
def shred(path: str | os.PathLike) -> bool:
    """Overwrite then delete. Best effort on SSDs, but the name and contents are gone."""
    p = Path(path)
    try:
        if p.is_symlink():
            p.unlink()
            return True
        if p.is_file():
            size = p.stat().st_size
            with open(p, "r+b") as fh:
                fh.write(b"\0" * min(size, 64 * 1024 * 1024))
                fh.flush()
                os.fsync(fh.fileno())
            p.unlink()
            return True
    except OSError:
        pass
    return False


class References:
    """Reference images for one project: ``<project>/refs``, private to the user."""

    TTL_S = 24 * 3600

    def __init__(self, project_dir: str | os.PathLike) -> None:
        self.dir = Path(project_dir) / "refs"
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.dir, 0o700)

    def path_for(self, ref_id: str, suffix: str = ".png") -> Path:
        return self.dir / f"{ref_id}{suffix}"

    def files(self) -> list[Path]:
        return sorted(p for p in self.dir.iterdir() if p.is_file()) if self.dir.exists() else []

    def delete_all(self) -> int:
        n = sum(1 for p in self.files() if shred(p))
        return n

    def sweep(self, now: Optional[float] = None, keep: Iterable[str] = ()) -> int:
        now = now or time.time()
        keep = set(keep)
        n = 0
        for p in self.files():
            if p.stem not in keep and now - p.stat().st_mtime > self.TTL_S:
                n += shred(p)
        return n


# ----------------------------------------------------------------------------- audit
_AUDIT_FIELDS = ("job", "state", "mode", "category", "provider", "refs", "version", "format", "ok",
                 "error", "op", "count", "seconds", "fidelity", "reason", "view", "source")


def _state_dir() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()


def audit(event: str, **fields: Any) -> None:
    """One line in 3d-studio-audit.jsonl, whitelisted fields only, scrubbed."""
    from ..selfrepair.jobs import scrub

    row: dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event[:40]}
    for key in _AUDIT_FIELDS:
        if key in fields and fields[key] is not None:
            v = fields[key]
            row[key] = v if isinstance(v, (bool, int, float)) else scrub(str(v), 80)
    path = _state_dir() / "3d-studio-audit.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass


def security_event(kind: str, **fields: Any) -> None:
    """A refusal worth knowing about (sensitive capture, rejected plan, injected text)."""
    audit(f"security.{kind}", **fields)
