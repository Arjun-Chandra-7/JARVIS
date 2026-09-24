"""Where away-mode messages come from and go to.

Each platform sits behind a ``Connector``. What matters about one is whether it can prove a reply
went out: the WhatsApp bridge returns the id the server assigned the message, so a WhatsApp reply
is ``sent`` only with that id in hand. A reply through a phone notification (KDE Connect's reply
action for Instagram, SMS, Telegram…) gets no acknowledgement at all, so it is recorded as
``submitted_unverified`` and the briefing says so — never "sent".
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("jarvis.away")

SENT, DRY_RUN, UNVERIFIED, FAILED = "sent", "dry_run", "submitted_unverified", "failed"


@dataclass
class InboundMessage:
    platform: str
    event_id: str
    thread_id: str
    sender_id: str
    sender_name: str
    text: str
    is_group: bool = False
    from_me: bool = False
    participant: str = ""
    ts: float = field(default_factory=time.time)
    group_name: str = ""
    reply_to: str = ""                # where a reply goes, when not the thread itself (a notification id)
    verified_source: bool = True      # produced by a registered connector, not typed in by a model


@dataclass
class SendResult:
    ok: bool
    status: str
    provider_id: str = ""
    error: str = ""

    @property
    def verified(self) -> bool:
        return self.status == SENT and bool(self.provider_id)


def fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join((text or "").lower().split()).encode()).hexdigest()[:16]


def mask(value: str) -> str:
    """For logs: never a whole number or JID."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"…{digits[-4:]}" if len(digits) >= 4 else hashlib.sha256(str(value).encode()).hexdigest()[:8]


class Connector:
    platform = ""
    verifies_delivery = False

    async def send(self, thread_id: str, text: str) -> SendResult:  # pragma: no cover - interface
        raise NotImplementedError

    def available(self) -> bool:
        return True


class WhatsAppConnector(Connector):
    """The local Baileys bridge (whatsapp/wa_service.js)."""

    platform = "whatsapp"
    verifies_delivery = True

    def __init__(self, client: Any = None) -> None:
        if client is None:
            from ..integrations import whatsapp as client
        self.client = client
        self._me: Optional[str] = None

    def available(self) -> bool:
        try:
            return bool(self.client.status())
        except Exception:  # noqa: BLE001
            return False

    async def send(self, thread_id: str, text: str) -> SendResult:
        if not await asyncio.to_thread(self.available):
            return SendResult(False, FAILED, error="WhatsApp bridge is not connected")
        try:
            r = await asyncio.to_thread(self.client.send, thread_id, text)
        except Exception as exc:  # noqa: BLE001
            r = {"ok": False, "error": str(exc)}
        if isinstance(r, dict) and r.get("ok") and r.get("id"):
            self._audit("sent", thread_id, text)
            return SendResult(True, SENT, provider_id=str(r["id"]))
        error = str((r or {}).get("error") or "the bridge did not acknowledge the message")[:200]
        self._audit("failed", thread_id, text, error)
        return SendResult(False, FAILED, error=error)

    def _audit(self, status: str, thread_id: str, text: str, error: str = "") -> None:
        try:  # the shared outbox log: hash and length only, never the text
            self.client._audit(f"away_{status}", "away-mode", thread_id, text, error)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ inbound
    def inbox(self) -> list[InboundMessage]:
        try:
            raw = self.client.inbox()
        except Exception:  # noqa: BLE001
            raw = []
        out = []
        for m in raw if isinstance(raw, list) else []:
            if not isinstance(m, dict) or not m.get("from"):
                continue
            jid = str(m.get("from"))
            if m.get("isNewsletter") or m.get("isStatus") or "@newsletter" in jid or jid == "status@broadcast":
                continue
            if m.get("fromMe"):
                continue   # in the inbox these are the owner's "message yourself" commands, not a chat
            group = bool(m.get("isGroup")) or jid.endswith("@g.us")
            out.append(InboundMessage(
                platform="whatsapp", event_id=str(m.get("id") or ""), thread_id=jid,
                sender_id=str(m.get("participant") or jid), sender_name=str(m.get("name") or ""),
                text=str(m.get("text") or ""), is_group=group, from_me=bool(m.get("fromMe")),
                participant=str(m.get("participant") or ""), ts=float(m.get("ts") or 0) / 1000.0 or time.time(),
                group_name=str(m.get("chatName") or "") if group else ""))
        return out

    def owner_outgoing(self, since: float, limit: int = 300) -> list[tuple[str, str, float]]:
        """(thread, message id, time) of messages sent from the owner's account since ``since``.

        The history holds everything sent from the account — the owner typing on the phone, and
        Jarvis's own replies; the caller tells them apart by the ids Jarvis recorded when sending.
        The owner's own "message yourself" chat is left out: that is where commands are typed.
        """
        try:
            rows = self.client.chats(limit)
        except Exception:  # noqa: BLE001
            rows = []
        me = self.me()
        out = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not r.get("fromMe"):
                continue
            jid, ts = str(r.get("from") or ""), float(r.get("ts") or 0) / 1000.0
            if not jid or ts < since or (me and jid == me):
                continue
            out.append((jid, str(r.get("id") or ""), ts))
        return out

    def me(self) -> str:
        if self._me is None:
            try:
                import httpx
                from ..integrations.whatsapp import _BASE
                self._me = str(httpx.get(f"{_BASE}/status", timeout=3).json().get("me") or "")
            except Exception:  # noqa: BLE001
                self._me = ""
        return self._me


class PhoneNotificationConnector(Connector):
    """Replies through a phone notification's reply action, over KDE Connect. No delivery receipt."""

    verifies_delivery = False

    def __init__(self, platform: str, kde: Any) -> None:
        self.platform = platform
        self.kde = kde

    def available(self) -> bool:
        return self.kde is not None

    async def send(self, thread_id: str, text: str) -> SendResult:
        if self.kde is None:
            return SendResult(False, FAILED, error="phone is not connected")
        try:
            await self.kde.reply(thread_id, text)
        except Exception as exc:  # noqa: BLE001
            return SendResult(False, FAILED, error=str(exc)[:200])
        return SendResult(True, UNVERIFIED)


class DryRunConnector(Connector):
    """Sends nothing; remembers what would have gone where. Used for tests and live dry runs."""

    def __init__(self, platform: str = "whatsapp", *, fail: Optional[Callable[[str, str], Optional[str]]] = None,
                 verifies: bool = True) -> None:
        self.platform = platform
        self.verifies_delivery = verifies
        self.outbox: list[dict[str, str]] = []
        self._fail = fail

    async def send(self, thread_id: str, text: str) -> SendResult:
        error = self._fail(thread_id, text) if self._fail else None
        if error:
            return SendResult(False, FAILED, error=error)
        pid = "dry-" + secrets.token_hex(4)
        self.outbox.append({"thread": thread_id, "text": text, "id": pid})
        logger.info("away dry-run: would reply on %s to %s (%d chars)", self.platform, mask(thread_id), len(text))
        return SendResult(True, DRY_RUN if self.verifies_delivery else UNVERIFIED, provider_id=pid)


def dry_run_forced() -> bool:
    return os.environ.get("JARVIS_DRY_RUN_SENDS", "").strip().lower() in {"1", "true", "yes", "on"}
