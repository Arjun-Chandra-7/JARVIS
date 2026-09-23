"""What the voice process writes down about itself — and what it must not.

The journal used to carry every transcript, every reply and the text of every phone message,
with only digit runs masked. That is a diary of the room. Now, by default:

* transcripts, replies, partials and message text become "(N words)";
* metrics — state transitions, latencies, which provider, confidence, interruptions, error
  categories, the audio device — go to a small JSONL file, and only fields on an allow-list are
  ever written there, so a transcript cannot slip in by being passed as a metric;
* raw audio is never written anywhere by this module.

``python -m jarvis --voice-diagnostics 20`` turns on a diagnostic mode for twenty minutes: the
journal then shows what was heard and said, still with numbers, e-mail addresses and long tokens
masked. It switches itself off when the time is up — a flag file holding its own expiry, which
the voice process reads, so nothing has to remember to turn it off.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

_TEXT_KINDS = {"heard", "reply", "partial", "transcript"}
MAX_MINUTES = 120
_METRIC_FIELDS = {
    "event", "state", "from_state", "ms", "s", "provider", "voice", "lang", "confidence", "kind",
    "reason", "device", "aec", "media", "route", "words", "streamed", "ok", "stage", "count",
    "onset_to_stop_ms", "onset_to_record_ms", "end_to_action_ms", "end_to_first_audio_ms",
    "detector_ms", "policy", "turn", "fallback",
}


def _runtime(name: str) -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / name


def _flag() -> Path:
    return _runtime("jarvis-voice-diagnostics")


def diagnostics_until() -> float:
    try:
        return float(_flag().read_text().strip() or 0)
    except (OSError, ValueError):
        return 0.0


def diagnostics_active(now: float | None = None) -> bool:
    until = diagnostics_until()
    if not until:
        return False
    if (now or time.time()) < until:
        return True
    try:
        _flag().unlink(missing_ok=True)       # expired: gone for good, not merely ignored
    except OSError:
        pass
    return False


def enable_diagnostics(minutes: float) -> float:
    minutes = max(1.0, min(float(minutes), MAX_MINUTES))
    until = time.time() + minutes * 60
    _flag().write_text(str(until))
    os.chmod(_flag(), 0o600)
    return until


def disable_diagnostics() -> None:
    _flag().unlink(missing_ok=True)


_MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"),
    (re.compile(r"\+?\d[\d\s-]{5,}\d"), "<number>"),
    (re.compile(r"\b[A-Za-z0-9_-]{24,}\b"), "<token>"),
]


def mask(text: str) -> str:
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    return text


def redact(text: str) -> str:
    """Words someone said, or Jarvis said, as they may appear in a log."""
    text = text or ""
    if diagnostics_active():
        return mask(text)
    words = len(re.findall(r"[\w']+", text))
    return f"({words} word{'s' if words != 1 else ''})"


def journal_line(kind: str, text: str) -> str:
    """The text part of a journal line for an event of ``kind``."""
    if kind in _TEXT_KINDS:
        return redact(text)
    if kind == "phone":
        # "WhatsApp from Papa: <message>" — who and where may be logged, what was said may not.
        head, sep, _ = (text or "").partition(": ")
        return mask(head) + (": " + redact(_) if sep else "")
    return mask(text or "")


# ------------------------------------------------------------------ metrics
def metrics_path() -> Path:
    base = Path(os.environ.get("JARVIS_STATE_DIR") or Path.home() / ".local/state/jarvis")
    return base / "voice-metrics.jsonl"


def metric(event: str, **fields: Any) -> dict:
    """Append one safe metric record. Fields not on the allow-list are dropped, not written."""
    record = {"t": round(time.time(), 3), "event": event}
    for key, value in fields.items():
        if key not in _METRIC_FIELDS:
            continue
        if isinstance(value, float):
            record[key] = round(value, 1)
        elif isinstance(value, (bool, int)) or value is None:
            record[key] = value
        elif isinstance(value, str):
            # Labels, not sentences: a short, masked token such as "groq" or "no_speech".
            record[key] = mask(value)[:40]
    try:
        path = metrics_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_suffix(".jsonl.1"))
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass
    return record


def recent(event: str = "", limit: int = 200) -> list[dict]:
    try:
        lines = metrics_path().read_text(encoding="utf-8").splitlines()[-5000:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not event or item.get("event") == event:
            out.append(item)
            if len(out) >= limit:
                break
    return list(reversed(out))
