"""Telling the owner something cannot wait.

An alert goes out on whichever of these the session allows: a desktop notification, a ping to the
paired phone over KDE Connect, and a short spoken line (queued in the state file for the voice
process, which owns the speaker). Alerts carry a name and a reason — never message text, never a
number. Only a verified emergency repeats, every five minutes, three times at most, until the
owner asks what is happening.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import datetime
from typing import Any, Callable, Optional

from .session import AwaySession, Store, tzinfo

logger = logging.getLogger("jarvis.away")

REPEAT_EVERY_S, MAX_REPEATS = 300.0, 3


def _run(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, capture_output=True, timeout=8).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def desktop(title: str, body: str, critical: bool) -> bool:
    exe = shutil.which("notify-send")
    if not exe:
        return False
    return _run([exe, "-a", "JARVIS", "-u", "critical" if critical else "normal", title, body])


def phone_ping(device_id: str, text: str) -> bool:
    exe = shutil.which("kdeconnect-cli")
    if not exe:
        return False
    argv = [exe, "--ping-msg", text] + (["-d", device_id] if device_id else ["-a"])
    return _run(argv)


def in_quiet_hours(session: AwaySession, now: Optional[float] = None) -> bool:
    if len(session.quiet_hours) != 2:
        return False
    moment = datetime.fromtimestamp(time.time() if now is None else now, tzinfo(session.timezone))
    here = moment.strftime("%H:%M")
    start, end = session.quiet_hours
    return (start <= here < end) if start <= end else (here >= start or here < end)


class Escalator:
    def __init__(self, config, *, desktop_fn: Optional[Callable[[str, str, bool], bool]] = None,
                 phone_fn: Optional[Callable[[str, str], bool]] = None) -> None:
        self.config = config
        # Looked up when used, so a test (or a dry run) can replace the module's functions.
        self.desktop_fn = desktop_fn or (lambda t, b, c: desktop(t, b, c))
        self.phone_fn = phone_fn or (lambda d, t: phone_ping(d, t))

    def queue(self, state: dict[str, Any], session: AwaySession, alert: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Decide and record an alert inside a store edit. Returns it if it should go out now."""
        level = alert.get("level", "urgent")
        rule = session.escalation_rules.get("interrupt", "urgent_only")
        if level == "info" and rule != "all":
            return None
        if level in {"urgent", "important"} and rule == "never":
            return None
        for existing in session.pending_escalations:
            if existing.get("thread") == alert.get("thread") and not existing.get("acknowledged"):
                if existing.get("level") == level or level != "emergency":
                    return None          # already alerted for this conversation
        alert = {**alert, "at": time.time(), "repeats": 0, "acknowledged": False}
        session.pending_escalations.append(alert)
        spoken = bool(session.escalation_rules.get("spoken", True)) and not in_quiet_hours(session)
        if spoken or level == "emergency":
            state["alerts"].append({"id": alert["id"], "text": alert["spoken"], "at": alert["at"], "said": False,
                                    "level": level})
        return alert

    def deliver(self, session: AwaySession, alert: dict[str, Any]) -> dict[str, bool]:
        """The side effects, outside the lock."""
        sent = {}
        critical = alert.get("level") == "emergency"
        if session.escalation_rules.get("desktop", True):
            sent["desktop"] = self.desktop_fn(alert["title"], alert["body"], critical or alert.get("level") == "urgent")
        if session.escalation_rules.get("phone", True):
            sent["phone"] = self.phone_fn(getattr(self.config, "kde_device_id", "") or "", alert["title"])
        logger.info("away escalation %s (%s): %s", alert.get("level"), alert.get("reason", ""),
                    ",".join(k for k, v in sent.items() if v) or "no channel")
        return sent

    def repeat_due(self, store: Store, now: Optional[float] = None) -> list[tuple[AwaySession, dict[str, Any]]]:
        """Verified emergencies not yet acknowledged, due for another alert."""
        now = time.time() if now is None else now
        due = []
        with store.edit() as state:
            raw = state.get("session")
            if not raw:
                return []
            session = AwaySession.from_dict(raw)
            for alert in session.pending_escalations:
                if alert.get("level") != "emergency" or alert.get("acknowledged"):
                    continue
                if alert.get("repeats", 0) >= MAX_REPEATS or now - float(alert.get("last", alert["at"])) < REPEAT_EVERY_S:
                    continue
                alert["repeats"] = alert.get("repeats", 0) + 1
                alert["last"] = now
                state["alerts"].append({"id": alert["id"], "text": alert["spoken"], "at": now, "said": False,
                                        "level": "emergency"})
                due.append(alert)
            state["session"] = session.to_dict()
        return [(session, a) for a in due]


def acknowledge_all(store: Store) -> int:
    with store.edit() as state:
        raw = state.get("session")
        n = 0
        if raw:
            for alert in raw.get("pending_escalations", []):
                if not alert.get("acknowledged"):
                    alert["acknowledged"] = True
                    n += 1
        for a in state["alerts"]:
            a["said"] = True
    return n


def take_spoken(store: Store) -> list[str]:
    """For the voice process: alerts not yet said, marked said."""
    if not store.path.exists():
        return []
    peek = store.read().get("alerts") or []
    if not any(not a.get("said") for a in peek):
        return []
    out = []
    with store.edit() as state:
        for a in state["alerts"]:
            if not a.get("said"):
                a["said"] = True
                out.append(str(a.get("text") or ""))
    return [t for t in out if t]
